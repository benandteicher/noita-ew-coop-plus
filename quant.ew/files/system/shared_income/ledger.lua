-- Pure run-scoped claim/payment journal. Never infers income from wallet deltas.
local store=dofile_once('mods/quant.ew/files/system/shared_income/persistence.lua')
local M={}
M.HISTORY=store.HISTORY -- bounded rollback window per recipient; older checkpoints fail closed
M.MAX=9007199254740991 -- largest exact Lua number; unsafe credits remain pending
function M.integer(n,max)
    return type(n)=='number' and n==n and n>=0 and n<= (max or M.MAX) and n==math.floor(n)
end
function M.new(run,enabled,reason)
    return {version=2,run=run,enabled=enabled,reason=reason,claims={},accounts={},chests={}}
end
function M.account(s,peer)
    s.accounts[peer]=s.accounts[peer]or{next=0,acked=0,pending={},history=s.version==2 and{}or nil,floor=s.version==2 and 0 or nil}
    return s.accounts[peer]
end
function M.amount(base,greed)
    if not M.integer(base,2147483647)or base<1 or not M.integer(greed,1024)then return end
    local n=base
    for _=1,greed do if n>math.floor(M.MAX/2)then return end;n=n*2 end
    return n
end
function M.claim(s,id,item,collector,greed,recipients)
    if not s.enabled or type(id)~='string'or s.claims[id]then return false end
    local amount=M.amount(item.value,greed)
    if not amount then return false,'Award exceeds exact monetary range; pickup remains pending' end
    -- Never evict unacknowledged money to make room. A long-disconnected peer
    -- can stop new awards at the bounded journal limit without losing a credit.
    for _,peer in ipairs(recipients)do
        local a=M.account(s,peer)
        if a.next>=2147483647 then return false,'Payment sequence limit reached; start a new run.'end
        if s.version==2 and a.next-(a.floor or 0)>=M.HISTORY and (a.floor or 0)>=math.min(a.acked,a.recovery or a.acked)then
            return false,'Recovery journal full; reconnect the recipient before collecting more gold.'
        end
    end
    s.claims[id]=true;store.touch(s.claims,id)
    for _,peer in ipairs(recipients)do
        local a=M.account(s,peer);a.next=a.next+1
        a.pending[a.next]={seq=a.next,id=id,amount=amount,collector=collector,value=item.value,hp=item.hp or 0,x=item.x,y=item.y}
        store.touch(a.pending,a.next)
        if s.version==2 then
            a.history[a.next]=a.pending[a.next];store.touch(a.history,a.next)
            local floor=math.max(0,a.next-M.HISTORY)
            for seq=(a.floor or 0)+1,floor do a.history[seq]=nil;store.touch(a.history,seq)end
            a.floor=floor
        end
    end
    return true
end
function M.ack(s,peer,seq)
    local a=s.accounts[peer]
    if not a or not M.integer(seq)or seq>a.next or seq<=a.acked then return false end
    local previous=a.acked;a.acked=seq
    for k=previous+1,seq do a.pending[k]=nil;store.touch(a.pending,k)end
    return true
end
function M.recover(s,peer,applied)
    local a=M.account(s,peer)
    if not M.integer(applied)or applied>a.next then return false,'Host journal is older than this body; restore matching paired checkpoints or start a new run.'end
    if applied<(a.floor or 0)then return false,'Body checkpoint is outside the 65,536-payment recovery window; restore a newer matching checkpoint or start a new run.'end
    for seq=applied+1,a.next do
        local p=a.history[seq]
        if type(p)~='table'or p.seq~=seq or not M.integer(p.amount)or type(p.id)~='string'then return false,'Recovery journal is incomplete; restore matching paired checkpoints or start a new run.'end
    end
    return true
end
function M.credit(balance,applied,payment)
    if not M.integer(payment.seq)or payment.seq~=applied+1 or not M.integer(payment.amount)then return end
    if not M.integer(balance)or balance>M.MAX-payment.amount then return end
    return balance+payment.amount
end
return M
