-- Pure run-scoped claim/payment journal. Never infers income from wallet deltas.
local M={}
M.MAX=9007199254740991 -- largest exact Lua number; unsafe credits remain pending
function M.integer(n,max)
    return type(n)=='number' and n==n and n>=0 and n<= (max or M.MAX) and n==math.floor(n)
end
function M.new(run,enabled,reason)
    return {version=1,run=run,enabled=enabled,reason=reason,claims={},accounts={},chests={}}
end
function M.account(s,peer)
    s.accounts[peer]=s.accounts[peer]or{next=0,acked=0,pending={}}
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
    s.claims[id]=true
    for _,peer in ipairs(recipients)do
        local a=M.account(s,peer);a.next=a.next+1
        a.pending[a.next]={seq=a.next,id=id,amount=amount,collector=collector,value=item.value,hp=item.hp or 0,x=item.x,y=item.y}
    end
    return true
end
function M.ack(s,peer,seq)
    local a=s.accounts[peer]
    if not a or not M.integer(seq)or seq>a.next or seq<=a.acked then return false end
    a.acked=seq
    for k in pairs(a.pending)do if k<=seq then a.pending[k]=nil end end
    return true
end
function M.credit(balance,applied,payment)
    if not M.integer(payment.seq)or payment.seq~=applied+1 or not M.integer(payment.amount)then return end
    if not M.integer(balance)or balance>M.MAX-payment.amount then return end
    return balance+payment.amount
end
return M
