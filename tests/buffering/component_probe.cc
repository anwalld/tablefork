#include <drogon/drogon.h>
#include <atomic>
#include <cstdlib>
#include <iostream>
#include "buffering/shared_queue.h"
#include "buffering/request_memo.h"
#include "services/room_service.h"
#include "services/ai_service.h"
#include "ai/ai_actions.h"
#include "services/message_service.h"

using namespace chat;
namespace {
void expect(bool condition, const char *what)
{
    if (!condition) throw std::runtime_error(what);
}
RequestContextPtr context(const std::string &id, const std::string &fingerprint=std::string(64,'a'))
{
    auto ctx=std::make_shared<RequestContext>();
    ctx->requestId=id; ctx->fingerprint=fingerprint;
    return ctx;
}
drogon::Task<> probe()
{
    auto db=drogon::app().getDbClient();
    auto user=co_await db->execSqlCoro("INSERT INTO users (username,password_hash) VALUES ('probe','unused')");
    auto member=co_await db->execSqlCoro("INSERT INTO members (type,user_id,display_name) VALUES ('human',?,'probe')",user.insertId());
    Session session{static_cast<int64_t>(user.insertId()),static_cast<int64_t>(member.insertId()),"probe"};
    session.request=context("room-committed-before-worker-loss");
    const auto first=co_await RoomService::create(session,"same-name");
    session.request=context("room-committed-before-worker-loss");
    const auto recovered=co_await RoomService::create(session,"same-name");
    expect(first==recovered,"SQL committed result was not recovered");
    session.request=context("room-committed-before-worker-loss",std::string(64,'b'));
    bool conflict=false;
    try { co_await RoomService::create(session,"different"); }
    catch(const ApiError &e) { conflict=e.code()=="request_conflict"; }
    expect(conflict,"parameter conflict accepted");
    std::vector<drogon::Task<Json::Value>> attempts;
    for(int i=0;i<8;++i)
    {
        auto concurrent=session;
        concurrent.request=context("concurrent-room");
        attempts.push_back(RoomService::create(concurrent,"concurrent"));
    }
    auto outcomes=co_await drogon::when_all(std::move(attempts));
    for(const auto &value:outcomes) expect(value==outcomes[0],"concurrent duplicate room");
    auto rows=co_await db->execSqlCoro("SELECT COUNT(*) AS n FROM rooms");
    expect(rows[0]["n"].as<int>()==2,"unexpected number of rooms");
    // If permit expires after a write but before commit, wrapper rolls back
    // both the memo and the business write rather than auto-committing on throw.
    bool allowed=true;
    auto ctx=context("rollback-on-permit-expiry");
    ctx->checkPermit=[&]{ if(!allowed) throw ApiError::unavailable(); };
    bool refused=false;
    try
    {
        auto tx=co_await newGuardedTransaction(ctx);
        co_await RequestMemo::begin(tx,ctx,"user:probe","probe.write");
        co_await tx->execSqlCoro("INSERT INTO rooms (name) VALUES ('must-rollback')");
        allowed=false;
        co_await RequestMemo::finish(tx,ctx,"user:probe",Json::objectValue);
        co_await commit(std::move(tx));
    }
    catch(const ApiError &) { refused=true; }
    expect(refused,"expired permit committed");
    rows=co_await db->execSqlCoro("SELECT COUNT(*) AS n FROM rooms WHERE name='must-rollback'");
    expect(rows[0]["n"].as<int>()==0,"transaction destructor committed partial business data");
    rows=co_await db->execSqlCoro("SELECT COUNT(*) AS n FROM business_request_results WHERE request_id='rollback-on-permit-expiry'");
    expect(rows[0]["n"].as<int>()==0,"rolled-back request memo remains");
    std::cout<<"PASS: SQL service recovery, 8 concurrent retries, parameter conflict, permit-expiry rollback\n";

    // Deterministic version of the CI cache-delete race: the ACK is already
    // durable, but its detached distribution starts only after room deletion.
    session.request=context("late-distribution-room");
    const auto room=co_await RoomService::create(session,"late-distribution");
    const auto roomId=room["id"].asInt64();
    const auto message=co_await MessageService::save(session,roomId,"late-cache-message","test-cache-body");
    session.request=context("delete-before-late-distribution");
    expect(co_await RoomService::leave(session,roomId,true),"test room was not deleted");
    co_await MessageService::distribute(message,context("delayed-distribution"));
    const auto cached=co_await drogon::app().getRedisClient()->execCommandCoro(
        "EXISTS %s",("room:"+std::to_string(roomId)+":recent").c_str());
    expect(cached.asInteger()==0,"late distribution repopulated a deleted room cache");
    std::cout<<"PASS: delayed distribution cannot repopulate a deleted room cache\n";

    session.request=context("private-role-a");
    const auto character=co_await AiService::createRole(session,"claude","Same name");
    session.request=context("private-role-a");
    const auto repeatedRole=co_await AiService::createRole(session,"claude","Same name");
    expect(character==repeatedRole,"private role request was not idempotent");
    session.request=context("private-role-b");
    const auto secondRole=co_await AiService::createRole(session,"claude","Same name");
    expect(character["id"]!=secondRole["id"],"same model/name collapsed character identities");
    co_await db->execSqlCoro("INSERT INTO user_api_keys (user_id,provider,ciphertext,nonce,last4) VALUES (?,'anthropic','unused',UNHEX(REPEAT('00',12)),'test')",session.userId);
    session.request=context("private-room");
    const auto privateRoom=co_await RoomService::create(session,"private room");
    const auto privateRoomId=privateRoom["id"].asInt64();
    const auto characterId=character["id"].asInt64();
    session.request=context("private-admit-old");
    co_await AiService::request(session,privateRoomId,std::to_string(characterId),"old persona");
    auto admissions=co_await db->execSqlCoro("SELECT HEX(admission_id) AS admission FROM room_ai_personas WHERE room_id=? AND member_id=?",privateRoomId,characterId);
    const auto oldAdmission=admissions[0]["admission"].as<std::string>();
    co_await db->execSqlCoro("INSERT INTO ai_generations (generation_id,room_id,member_id,payer_member_id,content,admission_id,persona_version,persona_snapshot) VALUES ('old-admission',?,?,?,'old output',UNHEX(?),1,'old persona')",privateRoomId,characterId,session.memberId,oldAdmission);
    session.request=context("private-remove");
    co_await AiService::remove(session,privateRoomId,std::to_string(characterId));
    session.request=context("private-admit-new");
    co_await AiService::request(session,privateRoomId,std::to_string(characterId),"new persona");
    admissions=co_await db->execSqlCoro("SELECT HEX(admission_id) AS admission FROM room_ai_personas WHERE room_id=? AND member_id=?",privateRoomId,characterId);
    const auto newAdmission=admissions[0]["admission"].as<std::string>();
    expect(oldAdmission!=newAdmission,"readmission reused the old generation fence");
    bool staleRejected=false;
    try { co_await MessageService::saveAiMessage(characterId,privateRoomId,"old-admission","old output",oldAdmission); }
    catch(const ApiError &e) { staleRejected=e.code()=="invalid_argument"; }
    expect(staleRejected,"old admission result was accepted after rejoin");
    co_await db->execSqlCoro("INSERT INTO ai_generations (generation_id,room_id,member_id,payer_member_id,content,admission_id,persona_version,persona_snapshot) VALUES ('new-admission',?,?,?,'new output',UNHEX(?),1,'new persona')",privateRoomId,characterId,session.memberId,newAdmission);
    const auto validFinal=co_await MessageService::saveAiMessage(characterId,privateRoomId,"new-admission","new output",newAdmission);
    const auto duplicateFinal=co_await MessageService::saveAiMessage(characterId,privateRoomId,"new-admission","new output",newAdmission);
    expect(!validFinal.duplicate && duplicateFinal.duplicate,"private final was not stored exactly once");
    rows=co_await db->execSqlCoro("SELECT COUNT(*) AS n FROM messages WHERE room_id=?",privateRoomId);
    expect(rows[0]["n"].as<int>()==1,"stale private generation polluted history");
    std::cout<<"PASS: private role identity/idempotency and old-admission final rejection after rejoin\n";
    std::atomic<int> subscriberReady{0},oldActions{0},currentChunk{0},currentFailed{0},burstChunks{0};
    const auto channel="room:"+std::to_string(privateRoomId);
    auto subscriber=drogon::app().getRedisClient()->newSubscriber();
    subscriber->subscribe(channel,[&](const std::string &,const std::string &payload) {
        if(payload=="persona-probe-ready") ++subscriberReady;
        if(payload.find("negative-chunk-proof")!=std::string::npos || payload.find("negative-failed-proof")!=std::string::npos || payload.find("old-admission")!=std::string::npos) ++oldActions;
        if(payload.find("positive-chunk-proof")!=std::string::npos) ++currentChunk;
        if(payload.find("positive-failed-proof")!=std::string::npos) ++currentFailed;
        if(payload.find("burst-chunk-proof")!=std::string::npos) ++burstChunks;
    });
    for(int i=0;i<100 && !subscriberReady.load();++i) {
        co_await drogon::app().getRedisClient()->execCommandCoro("PUBLISH %s %s",channel.c_str(),"persona-probe-ready");
        co_await drogon::sleepCoro(drogon::app().getLoop(),0.01);
    }
    expect(subscriberReady.load()>0,"action probe subscriber did not become ready");
    for(const auto &id:{std::string("negative-chunk-proof"),std::string("positive-chunk-proof")}) {
        const auto admission=id=="negative-chunk-proof"?oldAdmission:newAdmission;
        co_await db->execSqlCoro("INSERT INTO ai_generations (generation_id,room_id,member_id,payer_member_id,content,admission_id,persona_version,persona_snapshot) VALUES (?,?,?,?,'',UNHEX(?),1,'probe')",id,privateRoomId,characterId,session.memberId,admission);
        chat::ai::AiAction action;
        auto *chunk=action.mutable_chunk();chunk->set_room_id(privateRoomId);chunk->set_ai_member_id(characterId);
        chunk->set_generation_id(id);chunk->set_admission_id(admission);chunk->set_text("probe");
        handleAiAction(std::move(action));
    }
    chat::ai::AiAction staleFinal;
    auto *final = staleFinal.mutable_final();
    final->set_room_id(privateRoomId);
    final->set_ai_member_id(characterId);
    final->set_generation_id("old-admission");
    final->set_admission_id(oldAdmission);
    final->set_content("old output");
    handleAiAction(std::move(staleFinal));
    for(const auto &id:{std::string("negative-failed-proof"),std::string("positive-failed-proof")}) {
        chat::ai::AiAction action;
        auto *failed=action.mutable_failed();failed->set_room_id(privateRoomId);failed->set_ai_member_id(characterId);
        failed->set_generation_id(id);failed->set_admission_id(id=="negative-failed-proof"?oldAdmission:newAdmission);
        failed->set_notify_member_id(session.memberId);failed->set_code("probe");
        handleAiAction(std::move(action));
    }
    for(int i=0;i<200 && (!currentChunk.load() || !currentFailed.load());++i)
        co_await drogon::sleepCoro(drogon::app().getLoop(),0.01);
    co_await drogon::sleepCoro(drogon::app().getLoop(),0.5);
    expect(currentChunk.load()==1 && currentFailed.load()==1,"current-admission actions were not delivered");
    expect(oldActions.load()==0,"old-admission Chunk/Failed was broadcast after rejoin");
    // More concurrent readers than SQL connections must still finish. Each
    // action keeps its membership checks on the transaction connection.
    for (int i = 0; i < 32; ++i)
    {
        chat::ai::AiAction action;
        auto *chunk = action.mutable_chunk();
        chunk->set_room_id(privateRoomId);
        chunk->set_ai_member_id(characterId);
        chunk->set_generation_id("burst-chunk-proof");
        chunk->set_admission_id(newAdmission);
        chunk->set_text("probe");
        handleAiAction(std::move(action));
    }
    for (int i = 0; i < 300 && burstChunks.load() < 32; ++i)
        co_await drogon::sleepCoro(drogon::app().getLoop(), 0.01);
    expect(burstChunks.load() == 32, "concurrent chunks exhausted the SQL connection pool");
    subscriber->unsubscribe(channel);
    std::cout<<"PASS: real Chunk/Failed handlers publish current admission and reject retired admission\n";


    SharedQueue queue(drogon::app().getRedisClient("queue"),PayloadCrypto(std::string(64,'a')),"probe",SharedQueue::Options{});
    co_await queue.initialize();
    Json::Value descriptor;
    descriptor["method"]="POST"; descriptor["body"]="secret-test-password";
    auto task=co_await queue.admit(std::string(64,'c'),"request-1",descriptor,20);
    expect(task["state"]=="waiting","C++ admission failed");
    auto duplicate=co_await queue.admit(std::string(64,'c'),"request-1",descriptor,20);
    expect(task["id"]==duplicate["id"],"C++ admission did not dedup");
    auto denied=co_await queue.query(task["id"].asString(),std::string(64,'d'));
    expect(denied["error"]=="forbidden","C++ query owner check failed");
    co_await queue.control("","epoch",1,false);
    auto claimed=co_await queue.claim("epoch","worker","claim-token");
    expect(claimed["payload"]==descriptor,"C++ decrypt/Redis adapter corrupted payload");
    Json::Value result;
    result["status"]=200; result["cookies"].append("session-test-only");
    co_await queue.finish(task["id"].asString(),"worker","claim-token","succeeded",result);
    auto finished=co_await queue.query(task["id"].asString(),std::string(64,'c'));
    expect(finished["result"]==result,"C++ encrypted final result corrupted");
    std::cout<<"PASS: C++ Redis adapter, crypto, dedup, owner and final result\n";
}
} // namespace
int main(int argc, char **argv)
{
    if (argc == 2 && std::string(argv[1]) == "paused-ai-chunk")
    {
        setenv("BUFFER_REQUESTS_ENABLED", "1", 1);
        setenv("BUFFER_CONTROL_TOKEN", std::string(32, 'a').c_str(), 1);
        setenv("QUEUE_ENCRYPTION_KEY", std::string(64, 'a').c_str(), 1);
        // A fresh process has no running permit. A late stream fragment must
        // be rejected without terminating the event loop or requiring SQL.
        auto &app = drogon::app();
        app.registerBeginningAdvice([&] {
            chat::ai::AiAction action;
            auto *chunk = action.mutable_chunk();
            chunk->set_room_id(1);
            chunk->set_ai_member_id(1);
            chunk->set_generation_id("paused-chunk-probe");
            handleAiAction(std::move(action));
            app.getLoop()->runAfter(0.1, [&] {
                std::cout << "PASS: paused AI chunk leaves process alive\n";
                app.quit();
            });
        });
        app.run();
        return 0;
    }
    std::atomic<int> result{1};
    auto &app=drogon::app();
    app.createDbClient("mysql","mysql",3306,"chat","chat",std::getenv("MYSQL_PASSWORD"),16,"","default",false,"utf8mb4",10);
    app.createRedisClient(std::getenv("QUEUE_TEST_IP"),6379,"queue","",4,false,2);
    app.createRedisClient(std::getenv("QUEUE_TEST_IP"),6379,"default","",4,false,2);
    app.registerBeginningAdvice([&]{ drogon::async_run([&]() -> drogon::Task<> {
        try { co_await probe(); result=0; }
        catch(const std::exception &e) { std::cerr<<"FAIL: "<<e.what()<<'\n'; }
        app.quit();
        co_return;
    }); });
    app.run();
    return result;
}
