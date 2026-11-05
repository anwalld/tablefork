#include <drogon/drogon.h>
#include <atomic>
#include <cstdlib>
#include <iostream>
#include "buffering/shared_queue.h"
#include "buffering/request_memo.h"
#include "services/room_service.h"
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
    auto final=co_await queue.query(task["id"].asString(),std::string(64,'c'));
    expect(final["result"]==result,"C++ encrypted final result corrupted");
    std::cout<<"PASS: C++ Redis adapter, crypto, dedup, owner and final result\n";
}
} // namespace
int main()
{
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
