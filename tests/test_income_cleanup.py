"""Terminal requests vs pending ordered credit/effects; production adapter peers."""
import unittest
from test_shared_income import IncomePeers

ALIVE="settings.statue=false;if PEER=='B'then flags.ew_flag_notplayer_active=nil;entities[200].tags.ew_notplayer=nil end"
COUNT="""local send=SEND
SEND=function(method,data)calls.traffic=calls.traffic or{};calls.traffic[method]=(calls.traffic[method]or 0)+1;send(method,data)end
"""
class CleanupTests(unittest.TestCase):
    def peers(self):return IncomePeers(ALIVE+';'+COUNT)
    def quiet(self,p):
        for lua in p.peers.values():lua.execute('calls.traffic={}')
        p.tick(600)
        for lua in p.peers.values():
            for method in ('register','claim','retired'):
                self.assertEqual(lua.eval(f"calls.traffic['{method}'] or 0"),0,method)
    def saved(self,p):
        return {k:{n:lua.eval('globals')[n]for n in ('ew_income_run_v1','ew_income_owner_v1')if lua.eval('globals')[n]}for k,lua in p.peers.items()}
    def accept(self,p,who='A',id='42'):
        for lua in p.peers.values():lua.execute(f"gold=nugget('{id}',10);EntityAddComponent2(gold,'VariableStorageComponent',{{name='hp_value',value_float=.16}})")
        p.a.execute('frame=60;income.on_world_update()');p.flush()
        p.peers[who].execute('cross.ew_income_pickup(gold,ctx.my_player.entity)');p.flush()
    def test_review_reproduction_late_copy_never_creates_another_request(self):
        p=self.peers();p.pickup('A','42',10)
        p.b.execute("late=nugget('42',10);cross.ew_income_pickup(late,ctx.my_player.entity)")
        p.flush();self.quiet(p)
        self.assertEqual((p.money('A'),p.money('B')),(110,110))
        p.b.execute("local o=decode(globals.ew_income_owner_v1);assert(not o.claims['42']and not o.effects['42']and not entities[late].alive)")
    def test_retired_before_payment_moves_request_but_keeps_collector_effect(self):
        p=self.peers();self.accept(p)
        p.a.execute("local o=decode(globals.ew_income_owner_v1);assert(not o.claims['42']and o.effects['42'].life==0 and o.effects['42'].item.hp==.16)")
        self.assertEqual(p.money('A'),100)
        p.tick(35)
        p.a.execute("local o=decode(globals.ew_income_owner_v1);assert(not o.claims['42']and not o.effects['42']);assert(#EntityGetWithTag('ew_income_effect')==1)")
        self.assertEqual(p.money('A'),110);self.quiet(p)
    def test_competing_callbacks_leave_no_requests_or_double_effect(self):
        p=self.peers()
        for lua in p.peers.values():lua.execute("gold=nugget('88',10)")
        p.a.execute('frame=60;income.on_world_update()');p.flush()
        for lua in p.peers.values():lua.execute('cross.ew_income_pickup(gold,ctx.my_player.entity)')
        p.flush();p.tick(35);self.quiet(p)
        self.assertEqual((p.money('A'),p.money('B')),(110,110))
        for lua in p.peers.values():lua.execute("local o=decode(globals.ew_income_owner_v1);assert(not next(o.claims)and not next(o.effects))")
        self.assertEqual(p.a.eval("#EntityGetWithTag('ew_income_effect')"),1)
        self.assertEqual(p.b.eval("#EntityGetWithTag('ew_income_effect')"),0)
    def test_cold_reload_outstanding_credit_and_effect_then_duplicate_delivery(self):
        p=self.peers();self.accept(p)
        q=IncomePeers(ALIVE+';'+COUNT,self.saved(p));q.tick(35)
        self.assertEqual((q.money('A'),q.money('B')),(110,110))
        q.a.execute("assert(#EntityGetWithTag('ew_income_effect')==1)")
        q.a.execute("local s=decode(globals.ew_income_run_v1);receive('A','pay',encode({'A',s.run,{seq=1,id='42',amount=10,collector='A'}}))")
        q.flush();self.quiet(q)
        q.a.execute("assert(#EntityGetWithTag('ew_income_effect')==1)")
    def test_reload_legacy_retired_request_does_not_retry_or_repay(self):
        p=self.peers();p.pickup()
        for lua in p.peers.values():lua.execute("local o=decode(globals.ew_income_owner_v1);o.claims['42']={greed=0,item={value=10,hp=0,x=0,y=0},life=0};globals.ew_income_owner_v1=encode(o)")
        q=IncomePeers(ALIVE+';'+COUNT+";entities[100].c.WalletComponent.money=110;entities[200].c.WalletComponent.money=110",self.saved(p))
        self.quiet(q)
        self.assertEqual((q.money('A'),q.money('B')),(110,110))
        for lua in q.peers.values():lua.execute("assert(not next(decode(globals.ew_income_owner_v1).claims))")
    def test_late_retired_callback_preserves_still_pending_original_body_receipt(self):
        p=self.peers();self.accept(p,'B')
        p.b.execute("late=nugget('42',10);cross.ew_income_pickup(late,ctx.my_player.entity);local o=decode(globals.ew_income_owner_v1);assert(o.effects['42'].life==0 and not o.claims['42'])")
        p.flush();p.tick(35)
        self.assertEqual(p.money('B'),110)
        p.b.execute("assert(#EntityGetWithTag('ew_income_effect')==1)")

if __name__=='__main__':unittest.main()
