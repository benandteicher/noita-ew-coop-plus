"""Real income lifecycle hooks under controlled peer/message/body substitutes."""
import unittest
from lupa.luajit21 import LuaRuntime
from test_shared_income import IncomePeers, ROOT


def drain(p, hold=()):
    held=[]
    for _ in range(1000):
        if not p.q:return held
        packet=p.q.popleft();sender,method,data=packet
        if method in hold:held.append(packet);continue
        for name,lua in p.peers.items():
            if name!=sender:lua.globals().receive(sender,method,data)
    raise AssertionError('RPC loop')


def reload_client(p, checkpoint):
    lua=LuaRuntime(unpack_returned_tuples=True);lua.globals().PEER='B'
    lua.globals().READ=lambda n:(ROOT/n.removeprefix('mods/')).read_text(encoding='utf-8')
    lua.globals().SEND=lambda method,data:p.q.append(('B',method,data))
    lua.execute((ROOT/'tests/coop_fixture.lua').read_text())
    lua.execute((ROOT/'tests/income_fixture.lua').read_text())
    lua.execute('settings.statue=false')
    for k,v in checkpoint['B'].items():
        if not k.startswith('__'):lua.globals().globals[k]=v
    lua.globals().RESTORED_BODY=checkpoint['B']['__body']
    lua.execute('entities[ctx.my_player.entity]=decode(RESTORED_BODY)')
    lua.globals().flags.ew_flag_notplayer_active=checkpoint['B']['__body_dead']
    lua.execute('load_modules()');p.b=lua;p.peers['B']=lua
    p.a.execute('income.on_should_send_updates()')
    return drain(p,('resume_ready',))


def restore_living(p, saved):
    p.b.globals().SAVED_LIVING=saved
    # Exact public before/after hooks around restoration, including a killed
    # outgoing body as local_health.fake_unpolymorph does in production.
    p.b.execute('''local old=ctx.my_player.entity
    ctx.cap.shared_income.before_change(old)
    entities[300]=decode(SAVED_LIVING);entities[300].alive=true
    flags.ew_flag_notplayer_active=nil;ctx.my_player.entity=300
    entities[old].alive=false
    ctx.cap.shared_income.after_change(300)
    ''')


class RecoveryBodyChangeTests(unittest.TestCase):
    def scenario(self, receipt=True):
        p=IncomePeers('settings.statue=false')
        # Initial receipt was produced by the production adapter at wallet100,
        # before either payment. Preserve it on the pre-death living template.
        p.b.execute('''local living=decode(globals.ew_local_player_dead)
        living.vars=decode(encode(entities[ctx.my_player.entity].vars))
        SAVED_OLDER_LIVING=encode(living)''')
        saved=p.b.eval('SAVED_OLDER_LIVING') if receipt else p.b.eval('globals.ew_local_player_dead')
        p.pickup('A','95101',50);checkpoint=p.snapshot()
        self.assertEqual((p.money('B'),p.b.eval('decode(globals.ew_income_owner_v1).applied')),(150,1))
        p.pickup('A','95102',25)
        ready=reload_client(p,checkpoint)
        self.assertTrue(ready)
        self.assertEqual((p.money('A'),p.money('B')),(175,150))
        self.assertFalse(p.b.eval('ctx.cap.shared_income.enabled()'))
        self.assertEqual(p.b.eval('decode(globals.ew_income_owner_v1).applied'),1)
        return p,saved,ready

    def finish(self,p,ready):
        p.q.extend(ready);p.flush();p.tick(65)
        self.assertEqual((p.money('A'),p.money('B')),(175,175))
        self.assertEqual(p.b.eval('decode(globals.ew_income_owner_v1).applied'),2)
        self.assertEqual(p.ledger('.accounts.B.acked'),2)
        self.assertTrue(p.b.eval('ctx.cap.shared_income.enabled()'))

    def test_revival_before_resume_ready_without_living_receipt(self):
        p,saved,ready=self.scenario(False);restore_living(p,saved);self.finish(p,ready)

    def test_revival_before_resume_ready_with_legitimate_older_living_receipt(self):
        p,saved,ready=self.scenario(True);restore_living(p,saved);self.finish(p,ready)

    def test_revival_after_ready_before_replay_and_after_recovery(self):
        for phase in ('before_replay','after_recovery'):
            with self.subTest(phase=phase):
                p,saved,ready=self.scenario();p.q.extend(ready);p.flush()
                if phase=='after_recovery':p.tick(65)
                restore_living(p,saved);self.finish(p,[])

    def test_revival_after_partial_replay_and_duplicate_current_payments(self):
        p,saved,ready=self.scenario()
        for lua in p.peers.values():lua.execute("extra=nugget('95103',5)")
        p.a.execute('cross.ew_income_pickup(extra,100)');p.flush()
        p.q.extend(ready);p.flush()
        p.a.execute('frame=frame+35;income.on_world_update()')
        packets=drain(p,('pay',));self.assertTrue(packets)
        paid=[]
        for packet in packets:
            p.b.globals().PACKET=packet[2]
            if p.b.eval("decode(PACKET)[1]=='B'and decode(PACKET)[3].seq==2"):
                p.b.globals().receive(*packet);paid.append(packet)
        self.assertEqual(p.money('B'),175)
        restore_living(p,saved)
        p.q.extend(packets+paid);p.flush();p.tick(65)
        self.assertEqual(p.money('B'),180)
        self.assertEqual(p.b.eval('decode(globals.ew_income_owner_v1).applied'),3)

    def test_death_and_polymorph_replacements_during_recovery(self):
        for kind in ('death','polymorph','missing_wallet'):
            with self.subTest(kind=kind):
                p,saved,ready=self.scenario()
                restore_living(p,saved)
                p.b.globals().KIND=kind
                p.b.execute('''local old=ctx.my_player.entity
                if KIND~='polymorph'then ctx.cap.shared_income.before_change(old)end
                entities[301]={alive=true,x=0,y=0,tags={},c={}}
                if KIND~='missing_wallet'then entities[301].c.WalletComponent={money=1}end
                if KIND=='death'then entities[301].tags.ew_notplayer=true;flags.ew_flag_notplayer_active=true end
                ctx.my_player.entity=301;entities[old].alive=false
                if KIND=='polymorph'then income.on_local_player_polymorphed()
                else ctx.cap.shared_income.after_change(301)end
                ''')
                self.assertEqual(p.money('B'),150);self.finish(p,ready)

    def test_spending_during_handshake_is_carried_without_income_adjustment(self):
        p,saved,ready=self.scenario()
        p.b.execute('entities[ctx.my_player.entity].c.WalletComponent.money=130;income.on_world_update_post()')
        restore_living(p,saved);p.q.extend(ready);p.flush();p.tick(65)
        self.assertEqual((p.money('A'),p.money('B')),(175,155))
        self.assertEqual(p.b.eval('decode(globals.ew_income_owner_v1).applied'),2)

    def test_stale_session_and_duplicate_ready_do_not_recredit_replacement(self):
        p,saved,ready=self.scenario();restore_living(p,saved)
        p.b.execute('''local o=decode(globals.ew_income_owner_v1)
        receive('A','resume_ready',encode({'B',o.run,'old-nonce','old-token'}))
        receive('A','pay',encode({'B',o.run,{seq=2,id='stale',amount=999,collector='A'},'old-token'}))''')
        self.assertEqual(p.money('B'),150)
        self.finish(p,ready+ready)
        p.q.extend(ready);p.flush();p.tick(35)
        self.assertEqual(p.money('B'),175)

    def test_unvalidated_receipt_never_authorizes_wallet_transfer(self):
        p=IncomePeers('settings.statue=false');data=p.snapshot()
        p.b.globals().BODY=data['B']['__body']
        data['B']['__body']=p.b.eval("(function()local b=decode(BODY);for _,r in ipairs(b.vars)do if r.name=='ew_income_receipt_v2' then r.value_string='other-run'end end;return encode(b)end)()")
        ready=reload_client(p,data);self.assertFalse(ready)
        saved=p.b.eval('globals.ew_local_player_dead');restore_living(p,saved)
        self.assertEqual(p.money('B'),100)
        self.assertFalse(p.b.eval('ctx.cap.shared_income.enabled()'))

    def test_missing_wallet_before_validation_does_not_promote_globals(self):
        p=IncomePeers('settings.statue=false');p.pickup('A','95110',50);data=p.snapshot()
        p.b.globals().BODY=data['B']['__body']
        data['B']['__body']=p.b.eval("(function()local b=decode(BODY);b.c.WalletComponent=nil;for _,r in ipairs(b.vars)do if r.name=='ew_income_receipt_v2' then r.value_string='foreign-run'end end;return encode(b)end)()")
        ready=reload_client(p,data);self.assertFalse(ready)
        p.b.execute('''BEFORE=encode(entities[ctx.my_player.entity]);income.on_world_update();income.on_world_update_post()
        assert(encode(entities[ctx.my_player.entity])==BEFORE)
        assert(not entities[ctx.my_player.entity].c.WalletComponent)''')
        self.assertFalse(p.b.eval('ctx.cap.shared_income.enabled()'))

    def test_foreign_future_or_unannounced_body_is_not_stamped_or_credited(self):
        for bad in ('peer','run','future','unannounced'):
            with self.subTest(bad=bad):
                p,saved,ready=self.scenario();p.b.globals().SAVED=saved;p.b.globals().BAD=bad
                p.b.execute('''local old=ctx.my_player.entity
                ctx.cap.shared_income.before_change(old)
                entities[300]=decode(SAVED);ctx.my_player.entity=300
                for _,r in ipairs(entities[300].vars)do
                    if BAD=='peer'and r.name=='ew_income_receipt_owner_v2' then r.value_string='foreign'end
                    if r.name=='ew_income_receipt_v2' then
                        if BAD=='run'then r.value_string='foreign-run'end
                        if BAD=='future'then r.value_int=9 end
                    end
                end
                BEFORE=encode(entities[300]);entities[old].alive=false
                if BAD=='unannounced'then income.on_world_update_post()
                else ctx.cap.shared_income.after_change(300)end
                assert(encode(entities[300])==BEFORE)
                ''')
                p.q.extend(ready);p.flush();p.tick(35)
                self.assertEqual(p.money('B'),100)
                self.assertFalse(p.b.eval('ctx.cap.shared_income.enabled()'))

    def test_older_same_nonce_offer_and_ready_cannot_downgrade_new_session(self):
        p,saved,first=self.scenario();restore_living(p,saved)
        p.b.execute('frame=frame+121;income.on_world_update()')
        second=drain(p,('resume_ready',));self.assertTrue(second)
        p.q.extend(second+first);p.flush();p.tick(65)
        self.assertEqual(p.money('B'),175)
        session=p.b.eval('decode(globals.ew_income_owner_v1).session')
        p.b.globals().PACKET=first[0][2]
        p.b.execute("local a=decode(PACKET);local n=tonumber(a[4]:match('^(%d+):'));receive('A','resume_offer',encode({'B',a[2],a[3],n}));receive('A','resume_ready',PACKET)")
        p.flush();p.tick(35)
        self.assertEqual(p.b.eval('decode(globals.ew_income_owner_v1).session'),session)
        self.assertEqual(p.money('B'),175)


if __name__=='__main__':unittest.main()
