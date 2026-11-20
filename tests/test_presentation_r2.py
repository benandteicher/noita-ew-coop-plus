"""R2 cosmetics, passive UI and spectator ordering; simulated engine, not gameplay."""
import unittest
from test_coop_revival import Peers


class R2Tests(unittest.TestCase):
    def test_statue_help_and_watcher_messages_are_scoped_to_cooperative_mode(self):
        p=Peers('settings.statue=false')
        p.b.execute('''
        local s=dofile_once('mods/quant.ew/files/system/spectate/presentation.lua')
        ctx.proxy_opt.no_notplayer=false
        s.update('B',false,false);assert(calls.ui=='')
        ctx.proxy_opt.no_notplayer=true;ctx.proxy_opt.pvp=true
        s.update('B',false,false);assert(calls.ui=='')
        ctx.proxy_opt.pvp=false;s.update('B',false,false);assert(calls.ui:find('Your heart statue',1,true))
        ''')
    def test_pending_pulse_requires_this_carriers_completed_statue_transaction(self):
        p=Peers('settings.manual=true;settings.seconds=1')
        p.tick(20)
        p.b.execute("blocked=true;ctx.cap.coop_revival.chat_command('/revive')")
        p.flush();p.tick(1)
        self.assertEqual(p.state('.players.B.pending.reason'),'manual')
        self.assertFalse(p.a.eval('ctx.cap.coop_revival.presentation().pending'))
        q=Peers('settings.seconds=1')
        q.b.execute('blocked=true')
        q.a.execute("local f=dofile_once('mods/quant.ew/files/system/coop_revival/visuals.lua');f.draw=function(g,a)calls.progress=a[1] and a[1].progress end")
        q.tick(64)
        self.assertTrue(q.a.eval('ctx.cap.coop_revival.presentation().pending'))
        self.assertEqual(q.a.eval('calls.progress'),1)
        self.assertIsNone(q.b.eval('calls.deserialize'))
        self.assertEqual(q.state('.players.B.run'),0)
        q.b.execute('blocked=false');q.tick(10)
        self.assertEqual(q.b.eval('calls.deserialize'),1)

    def test_cancel_tombstone_and_new_attempt_reject_late_packets(self):
        p=Peers('settings.statue=false')
        p.a.execute('''
        local f=dofile_once('mods/quant.ew/files/system/coop_revival/visuals.lua')
        f.draw=function(g,a)calls.active=#a;calls.progress=a[1] and a[1].progress end
        f.accept_progress('A','B',1,.2,0,10,1);f.update({});assert(calls.active==1)
        f.accept_progress('A',nil,nil,nil,1,10,3)
        f.accept_progress('A','B',1,.8,2,10,2);f.update({});assert(calls.active==0)
        f.accept_progress('A','B',1,.1,3,11,1);f.update({});assert(calls.active==1 and calls.progress==.1)
        f.accept_progress('A',nil,nil,nil,4,10,99);f.update({});assert(calls.active==1)
        f.accept_progress('A','B',1,.9,4,11,0);f.update({});assert(calls.progress==.1)
        frame=31;f.update({});assert(calls.active==1)
        frame=34;f.update({});assert(calls.active==0)
        ''')
        self.assertIsNone(p.state('.players.B.pending'))

    def test_body_anchor_does_not_follow_item_or_facing_fallback(self):
        p=Peers('settings.statue=false')
        p.a.execute('''
        local f=dofile_once('mods/quant.ew/files/system/coop_revival/visuals.lua')
        for _,sx in ipairs({80,120,1000})do
         entities[200].x=sx;entities[200].y=90
         local x,y=f.anchor('A','B');assert(x==100 and y==96)
        end
        entities[100].x=145;entities[100].y=130
        local x,y=f.anchor('A','B');assert(x==145 and y==126)
        entities[100].c.Inventory2Component.mActiveItem=0;assert(not f.anchor('A','B'))
        ''')

    def test_reduced_wisps_keep_stationary_progress_and_optional_numeric(self):
        p=Peers('settings.ritual_sound=false;settings.revive_effects=false')
        p.a.execute('''
        local f=dofile_once('mods/quant.ew/files/system/coop_revival/visuals.lua')
        local o={w=427,h=240,cx=100,cy=100,scale=1,time=1,now=0,decorative=false}
        calls.images={};f.draw(1,{{x=100,y=96,progress=.25}},{},o)
        assert(#calls.images==9);local x,y=calls.images[3].x,calls.images[3].y
        calls.images={};o.time=100;f.draw(1,{{x=100,y=96,progress=.75}},{},o)
        assert(#calls.images==21 and calls.images[3].x==x and calls.images[3].y==y)
        o.numeric=true;calls.ui='';f.draw(1,{{x=100,y=96,progress=.5}},{},o);assert(calls.ui=='50%\\n')
        ''')
        p.tick(320)
        self.assertEqual(p.b.eval('calls.deserialize'),1)
        self.assertIsNone(p.a.eval('calls.last_sound'))

    def test_visible_upper_hud_region_is_not_offscreen_and_no_duplicate_bar(self):
        p=Peers('settings.statue=false')
        p.a.execute('''
        local m=dofile_once('mods/quant.ew/files/system/player_arrows/teammate_model.lua')
        local x,y,off=m.project(100,0,100,100,427,240,1);assert(y==20 and not off)
        local u=dofile_once('mods/quant.ew/files/system/player_arrows/teammates.lua')
        calls.images={};calls.ui=''
        local o={w=427,h=240,cx=100,cy=100,scale=1,now=0,opacity=.4,mx=213,my=132}
        u.draw(1,{{peer='B',name='Long Name',x=100,y=100,hp=1.52,max_hp=8,state={}}},nil,o)
        assert(calls.ui:find('38 / 200',1,true) and not calls.ui:find('HP',1,true) and #calls.images==1) -- compact numeric frame only; no heart or added bar
        calls.images={};calls.ui=''
        u.draw(1,{{peer='B',x=100,y=100,dead=true,state={}}},nil,o)
        assert(calls.ui=='' and #calls.images==0)
        ''')

    def test_configured_spectator_bindings_hint_fades_and_resets_each_death(self):
        p=Peers('settings.statue=false')
        p.b.execute('''
        local s=dofile_once('mods/quant.ew/files/system/spectate/presentation.lua')
        virtual_files['data/scripts/debug/keycodes.lua']='Key_Q = 20\\nKey_E = 8\\nKey_R = 21\\n'
        settings['quant.ew.rebind_lspectate']='20';settings['quant.ew.rebind_rspectate']='8';settings['quant.ew.rebind_sspectate']='21'
        s.update('B',false,false);assert(calls.ui:find('[Q] previous',1,true) and calls.ui:find('[R] your statue',1,true))
        frame=301;s.update('B',false,false);assert(not calls.ui:find('previous',1,true))
        flags.ew_flag_notplayer_active=nil;s.update('B',false,false);assert(calls.ui=='')
        flags.ew_flag_notplayer_active=true;frame=302;s.update('B',false,false);assert(calls.ui:find('previous',1,true))
        s.update('A',true,false);assert(calls.ui:find('Watching:',1,true))
        s.update('A',false,true);assert(calls.ui=='')
        ''')

    def test_watched_status_validates_death_connection_sequence_and_expiry(self):
        p=Peers('settings.statue=false')
        p.a.execute('''
        local s=dofile_once('mods/quant.ew/files/system/spectate/presentation.lua')
        s.draw=function(g,v)calls.watched=v.count end
        s.accept('A','A',1,0);s.update('A',false,false);assert(calls.watched==0)
        s.accept('B','A',1,0);s.update('A',false,false);assert(calls.watched==1)
        s.accept('B','B',2,1);s.accept('B','A',1,2);s.update('A',false,false);assert(calls.watched==0)
        s.accept('B','A',3,2);s.update('A',false,false);assert(calls.watched==1)
        frame=153;s.update('A',false,false);assert(calls.watched==0)
        s.accept('B','A',4,154);ctx.players.B.dc=true;s.update('A',false,false);assert(calls.watched==0)
        ctx.players.B.dc=false;ctx.players.B.status.is_alive=true
        s.accept('B','A',5,155);s.update('A',false,false);assert(calls.watched==0)
        ''')

    def test_spectator_switch_keys_respect_chat_menus_and_streaming_request(self):
        p=Peers('settings.statue=false')
        p.tick(2)
        p.b.execute("InputIsKeyJustDown=function(k)return k==55 end;ctx.is_texting=true")
        p.tick(1);self.assertEqual(p.b.eval('ctx.spectating_over_peer_id'),'B')
        p.b.execute('ctx.is_texting=false;ctx.is_paused=true')
        p.tick(1);self.assertEqual(p.b.eval('ctx.spectating_over_peer_id'),'B')
        p.b.execute('ctx.is_paused=false')
        p.tick(1);self.assertEqual(p.b.eval('ctx.spectating_over_peer_id'),'A')
        p.b.execute("InputIsKeyJustDown=function(k)return k==56 end")
        p.tick(1);self.assertEqual(p.b.eval('ctx.spectating_over_peer_id'),'B')
        p.b.execute("InputIsKeyJustDown=function(k)return k==55 end;spectate.request_camera_position(900,100)")
        p.tick(1);self.assertEqual(p.b.eval('ctx.spectating_over_peer_id'),'B')
        self.assertEqual(p.b.eval('calls.camera[1]'),900)

if __name__=='__main__':unittest.main()
