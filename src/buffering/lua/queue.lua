-- One script is the sole writer of queue accounting and state. Arguments are
-- authenticated by the app, never passed through from a public JSON body.
local ns, action, a = KEYS[1], ARGV[1], cjson.decode(ARGV[2])
local meta, waiting, deadlines, running, results = ns..':meta', ns..':waiting', ns..':deadlines', ns..':running', ns..':results'
local recovery = ns..':recovery'
local time = redis.call('TIME')
local now = tonumber(time[1])*1000 + math.floor(tonumber(time[2])/1000)
local function response(value) return cjson.encode(value) end
local function key(id) return ns..':task:'..id end
local function load(id)
    local raw = redis.call('GET', key(id))
    if raw then return cjson.decode(raw) end
end
local function save(t) redis.call('SET', key(t.id), cjson.encode(t)) end
if action == 'init' then
    if not redis.call('HGET', meta, 'incarnation') then
        redis.call('HSET', meta, 'incarnation', a.incarnation, 'mode', 'paused', 'epoch', '', 'revision', 0,
                   'body_bytes', 0, 'memory', 0, 'sequence', 0, 'config', cjson.encode(a.config))
    end
    return response({incarnation=redis.call('HGET',meta,'incarnation')})
end
if not redis.call('HGET', meta, 'incarnation') then return response({error='queue_restarted'}) end
local cfg = cjson.decode(redis.call('HGET',meta,'config'))
local function terminal(t, state, result)
    redis.call('ZREM',waiting,t.id)
    redis.call('ZREM',deadlines,t.id)
    redis.call('ZREM',running,t.id)
    redis.call('ZREM',recovery,t.id)
    -- Payload is no longer needed after completion, including unknown.
    local newCharge = 2048 + #(result or '')
    redis.call('HINCRBY',meta,'memory',newCharge-t.charge)
    t.charge, t.payload, t.state, t.result = newCharge, nil, state, result
    t.claim, t.worker, t.claim_deadline = nil, nil, nil
    save(t)
    redis.call('ZADD',results,now+cfg.result_ms,t.id)
end
-- Bounded batches avoid a long Lua pause. Expired entries never become direct
-- traffic or executable just because a batch has not yet visited them.
for _,id in ipairs(redis.call('ZRANGEBYSCORE',results,'-inf',now,'LIMIT',0,128)) do
    local t=load(id)
    if t then redis.call('HINCRBY',meta,'memory',-t.charge) end
    redis.call('DEL',key(id)); redis.call('ZREM',results,id)
end
for _,id in ipairs(redis.call('ZRANGEBYSCORE',deadlines,'-inf',now,'LIMIT',0,128)) do
    local t=load(id)
    if t and t.state=='waiting' then
        redis.call('HINCRBY',meta,'body_bytes',-t.body_bytes)
        terminal(t,'expired',nil)
    else redis.call('ZREM',deadlines,id) end
end
for _,id in ipairs(redis.call('ZRANGEBYSCORE',running,'-inf',now,'LIMIT',0,128)) do
    local t=load(id)
    if t and t.state=='running' then
        if t.recoverable then
            -- Only the trusted dispatch registry may classify an operation
            -- recoverable. SQL uniqueness/memo must fence an old live worker.
            t.state,t.claim,t.worker='recovering',nil,nil
            save(t); redis.call('ZREM',running,id)
            redis.call('ZADD',recovery,t.sequence,id)
        else terminal(t,'unknown',nil) end
    else redis.call('ZREM',running,id) end
end
local function maybeDirect()
    if redis.call('HGET',meta,'mode')=='draining' and redis.call('ZCARD',waiting)==0 and redis.call('ZCARD',running)==0 and redis.call('ZCARD',recovery)==0 then
        redis.call('HSET',meta,'mode','direct')
    end
end
if action=='status' then
    maybeDirect()
    return response({mode=redis.call('HGET',meta,'mode'),epoch=redis.call('HGET',meta,'epoch'),
        revision=tonumber(redis.call('HGET',meta,'revision')),incarnation=redis.call('HGET',meta,'incarnation'),
        waiting=redis.call('ZCARD',waiting),running=redis.call('ZCARD',running),
        body_bytes=tonumber(redis.call('HGET',meta,'body_bytes')),memory=tonumber(redis.call('HGET',meta,'memory'))})
end
if action=='control' then
    local epoch=redis.call('HGET',meta,'epoch')
    local revision=tonumber(redis.call('HGET',meta,'revision'))
    if a.expected_epoch~=epoch or (a.epoch==epoch and a.revision<=revision) then
        return response({error='stale_control'})
    end
    if a.mode~='paused' and a.mode~='draining' then return response({error='invalid_mode'}) end
    redis.call('HSET',meta,'epoch',a.epoch,'revision',a.revision,'mode',a.mode)
    maybeDirect()
    return response({ok=true})
end
if action=='admit' then
    local old=load(a.id)
    if old then
        if old.owner~=a.owner then return response({error='forbidden'}) end
        if old.binding~=a.binding then return response({error='request_conflict'}) end
        return response({id=old.id,state=old.state})
    end
    maybeDirect()
    if redis.call('HGET',meta,'mode')=='direct' then
        if not a.force_queue then return response({direct=true}) end
        redis.call('HSET',meta,'mode','draining')
    end
    local charge=2048+#a.payload
    if a.body_bytes<0 or a.body_bytes>cfg.max_body_bytes or charge>cfg.max_memory then
        return response({error='queue_full'})
    end
    if redis.call('ZCARD',waiting)>=cfg.max_waiting or tonumber(redis.call('HGET',meta,'body_bytes'))+a.body_bytes>cfg.max_body_bytes or
       tonumber(redis.call('HGET',meta,'memory'))+charge>cfg.max_memory then
        return response({error='queue_full'})
    end
    local seq=redis.call('HINCRBY',meta,'sequence',1)
    local t={id=a.id,owner=a.owner,binding=a.binding,payload=a.payload,body_bytes=a.body_bytes,
             charge=charge,state='waiting',sequence=seq,enqueued_at=now,deadline=now+cfg.wait_ms,recoverable=a.recoverable==true}
    save(t)
    redis.call('ZADD',waiting,seq,a.id)
    redis.call('ZADD',deadlines,t.deadline,a.id)
    redis.call('HINCRBY',meta,'body_bytes',a.body_bytes)
    redis.call('HINCRBY',meta,'memory',charge)
    return response({id=a.id,state='waiting'})
end
if action=='query' then
    local t=load(a.id)
    local expires=redis.call('ZSCORE',results,a.id)
    if t and expires and tonumber(expires)<=now then
        redis.call('HINCRBY',meta,'memory',-t.charge)
        redis.call('DEL',key(a.id)); redis.call('ZREM',results,a.id); t=nil
    end
    if not t then return response({error='result_unknown'}) end
    if t.state=='waiting' and t.deadline<=now then
        redis.call('HINCRBY',meta,'body_bytes',-t.body_bytes)
        terminal(t,'expired',nil)
    end
    if t.owner~=a.owner then return response({error='forbidden'}) end
    return response({id=t.id,state=t.state,result=t.result})
end
if action=='claim' then
    if redis.call('HGET',meta,'mode')~='draining' or redis.call('HGET',meta,'epoch')~=a.epoch then
        return response({empty=true})
    end
    -- Sweep may leave expired work behind a bounded batch; never claim it.
    for i=1,128 do
        local recovering=false
        local head=redis.call('ZRANGE',recovery,0,0)
        if #head>0 then recovering=true else head=redis.call('ZRANGE',waiting,0,0) end
        if #head==0 then maybeDirect(); return response({empty=true}) end
        local t=load(head[1])
        if not t then return response({error='queue_corrupt'}) end
        if not recovering then redis.call('HINCRBY',meta,'body_bytes',-t.body_bytes) end
        if not recovering and t.deadline<=now then terminal(t,'expired',nil)
        else
            redis.call('ZREM',recovery,t.id)
            redis.call('ZREM',waiting,t.id); redis.call('ZREM',deadlines,t.id)
            t.wait_ms=math.max(0,now-(t.enqueued_at or now))
            t.state,t.worker,t.claim,t.claim_deadline='running',a.worker,a.claim,now+cfg.claim_ms
            save(t); redis.call('ZADD',running,t.claim_deadline,t.id)
            return response({id=t.id,payload=t.payload,claim=t.claim,wait_ms=t.wait_ms})
        end
    end
    return response({empty=true})
end
if action=='renew' or action=='finish' then
    local t=load(a.id)
    if not t or t.state~='running' or t.claim~=a.claim or t.worker~=a.worker or t.claim_deadline<=now then
        return response({error='lost_claim'})
    end
    if action=='renew' then
        t.claim_deadline=now+cfg.claim_ms; save(t); redis.call('ZADD',running,t.claim_deadline,t.id)
    else
        if a.state~='succeeded' and a.state~='failed' and a.state~='unknown' then return response({error='invalid_state'}) end
        if a.state=='unknown' and t.recoverable then
            t.state,t.claim,t.worker='recovering',nil,nil
            redis.call('ZREM',running,t.id)
            redis.call('ZADD',recovery,t.sequence,t.id)
            save(t)
            return response({ok=true})
        end
        local charge=2048+#(a.result or '')
        if #(a.result or '')>cfg.max_result_bytes or tonumber(redis.call('HGET',meta,'memory'))-t.charge+charge>cfg.max_memory then
            terminal(t,'unknown',nil)
        else terminal(t,a.state,a.result) end
        maybeDirect()
    end
    return response({ok=true})
end
return response({error='invalid_action'})
