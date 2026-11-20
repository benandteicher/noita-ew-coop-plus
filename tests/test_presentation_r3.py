"""Focused R3 state tests. Native appearance/HUD evidence remains separate."""
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
from test_coop_revival import Peers,ROOT

class R3Tests(unittest.TestCase):
    def test_orphaned_local_pending_seal_expires_without_consuming_recovery(self):
        p=Peers('settings.statue=false')
        p.a.execute('''
        local f=dofile_once('mods/quant.ew/files/system/coop_revival/visuals.lua')
        f.draw=function(g,a)calls.active=#a;calls.progress=a[1]and a[1].progress end
        f.update({channel={target='B',death=1,progress=.99}})
        frame=1;f.update({pending=true});assert(calls.active==1 and calls.progress==1)
        frame=300;f.update({pending=true});assert(calls.active==1)
        frame=301;f.update({pending=true});assert(calls.active==0)
        frame=302;f.update({pending=true});assert(calls.active==0)
        ''')
        self.assertIsNone(p.state('.players.B.pending'))
        self.assertEqual(p.state('.players.B.run'),0)
    def test_local_progress_is_fresh_each_update_with_bounded_messages(self):
        p=Peers()
        p.a.execute('''
        local f=dofile_once('mods/quant.ew/files/system/coop_revival/visuals.lua')
        calls.values={};local accept=f.accept_progress
        f.accept_progress=function(...)calls.packets=(calls.packets or 0)+1;return accept(...)end
        f.draw=function(g,a)if a[1]then calls.values[#calls.values+1]=a[1].progress;assert(#a==1)end end
        ''')
        p.tick(60)
        values=list(p.a.globals().calls['values'].values())
        self.assertEqual(len(values),60);self.assertEqual(len(set(values)),60)
        self.assertEqual(p.a.eval('calls.packets'),6)
        self.assertIsNone(p.b.eval('calls.deserialize'))

    def test_remote_blend_is_bounded_by_samples_and_expires(self):
        p=Peers('settings.statue=false;settings.revive_effects=false')
        p.a.execute('''
        local f=dofile_once('mods/quant.ew/files/system/coop_revival/visuals.lua')
        f.draw=function(g,a)calls.progress=a[1]and a[1].progress;calls.active=#a end
        f.accept_progress('A','B',1,.2,0,1,1)
        f.accept_progress('A','B',1,.4,10,1,2)
        local last=.19
        for now=10,20 do frame=now;f.update({});assert(calls.progress>=last and calls.progress<=.4);last=calls.progress end
        assert(calls.progress==.4)
        frame=41;f.update({});assert(calls.active==0)
        f.accept_progress('A',nil,nil,nil,42,1,3)
        f.accept_progress('A','B',1,.9,43,1,2);f.update({});assert(calls.active==0)
        ''')

    def test_native_particle_pool_reuses_bounds_and_clears(self):
        p=Peers('settings.statue=false')
        p.a.execute('''
        local f=dofile_once('mods/quant.ew/files/system/coop_revival/particles.lua')
        local a={{x=100,y=96,sx=110,sy=95,progress=.5,key='a'}}
        for now=1,100 do f.update(a,now,true)end
        assert(calls.emitters_created==19)
        for e=1001,1019 do assert(entities[e].alive and entities[e].tags.ew_no_enemy_sync and entities[e].c.LifetimeComponent.kill_frame==130)end
        f.update(a,101,false)
        for e=1001,1019 do assert(not entities[e].alive)end
        for i=2,7 do a[i]={x=100,y=96,progress=.8,key=tostring(i)}end
        f.update(a,102,true);assert(calls.emitters_created==95) -- at most four pools
        f.update({},103,true)
        for e=1001,1095 do assert(not entities[e].alive)end
        ''')
        xml=ET.parse(ROOT/'quant.ew/files/system/coop_revival/ritual/emitter.xml').getroot()
        self.assertEqual([c.tag for c in xml],['ParticleEmitterComponent','LifetimeComponent'])
        emitter=xml.find('ParticleEmitterComponent')
        for key in ['create_real_particles','emit_real_particles','collide_with_grid','collide_with_gas_and_fire']:
            self.assertEqual(emitter.get(key),'0')
        self.assertLessEqual(float(emitter.get('lifetime_max')),.45)

    def test_notice_has_entrance_hold_exit_and_each_queued_lifetime(self):
        p=Peers('settings.statue=false')
        p.a.execute('''
        local u=dofile_once('mods/quant.ew/files/system/player_arrows/teammates.lua')
        local m=dofile_once('mods/quant.ew/files/system/player_arrows/teammate_model.lua')
        u.announce('One','1');u.announce('Two','2');u.announce('Three','3')
        local n=u.current_notice(0,false);assert(n.name=='One'and m.notice_alpha(n,0)==0)
        assert(m.notice_alpha(n,4)>0 and m.notice_alpha(n,4)<1)
        assert(m.notice_alpha(n,9)==1 and m.notice_alpha(n,125)==1)
        assert(m.notice_alpha(n,140)>0 and m.notice_alpha(n,140)<1 and m.notice_alpha(n,150)==0)
        n=u.current_notice(150,false);assert(n.name=='Two'and n.started==150)
        n=u.current_notice(300,false);assert(n.name=='Three'and n.started==300)
        assert(not u.current_notice(450,false))
        u.announce('Hidden','4');assert(not u.current_notice(451,true));assert(not u.current_notice(452,false))
        ''')

    def test_hp_format_is_readable_positive_and_float_noise_safe(self):
        p=Peers('settings.statue=false')
        p.a.execute('''
        local m=dofile_once('mods/quant.ew/files/system/player_arrows/teammate_model.lua')
        assert(m.hp(1.5199999809)=='38'and m.hp(8)=='200')
        assert(m.hp(.0004)=='0.01'and m.hp(.0000001)=='0.0000025')
        assert(m.hp(40000)=='1,000,000'and m.hp(0)=='0')
        ''')

    def test_passive_hover_hysteresis_and_exit_do_not_draw_extra_bar(self):
        p=Peers('settings.statue=false')
        p.a.execute('''
        local u=dofile_once('mods/quant.ew/files/system/player_arrows/teammates.lua')
        calls.images={};calls.ui=''
        local list={{peer='B',name='Unused',x=100,y=100,hp=1.52,max_hp=8,state={}}}
        local o={w=427,h=240,cx=100,cy=100,scale=1,now=1,opacity=.5,mx=213,my=132}
        u.draw(1,list,nil,o);assert(calls.ui:find('38 / 200',1,true))
        calls.ui='';o.now=2;o.mx=221;u.draw(1,list,nil,o);assert(calls.ui:find('38 / 200',1,true))
        o.mx=300;o.now=3;u.draw(1,list,nil,o)
        calls.ui='';o.now=12;u.draw(1,list,nil,o)
        calls.ui='';o.now=13;u.draw(1,list,nil,o);assert(calls.ui=='')
        ''')

    def test_edge_layout_avoids_native_hud_and_stays_near_anchor(self):
        p=Peers('settings.statue=false')
        p.a.execute('''
        local m=dofile_once('mods/quant.ew/files/system/player_arrows/teammate_model.lua')
        local reserve={{x=0,y=0,w=154,h=54},{x=313,y=0,w=114,h=58}}
        for _,v in ipairs({{10,10},{410,10},{10,220},{410,220},{210,10},{210,220},{10,120},{410,120}})do
         local b=m.edge_place(v[1],v[2],427,240,{},reserve)
         assert(b and b.x>=8 and b.y>=8 and b.x+b.w<=419 and b.y+b.h<=232)
         for _,r in ipairs(reserve)do assert(not m.overlap(b,r))end
        end
        local a=m.edge_place(380,120,427,240,{},reserve);local b=m.edge_place(381,121,427,240,{},reserve)
        assert(math.abs(a.x-b.x)<=2 and math.abs(a.y-b.y)<=2)
        ''')

    def test_inventory_view_restores_borrowed_native_component(self):
        p=Peers('settings.statue=false')
        p.a.execute('''
        local v=dofile_once('mods/quant.ew/files/system/spectate/inventory.lua')
        local c=EntityAddComponent2(100,'InventoryGuiComponent',{mActive=true,_enabled=false})
        assert(v.acquire(100)==c and c._enabled==true)
        ComponentSetValue2(c,'mActive',false);v.release()
        assert(entities[100].c.InventoryGuiComponent==c and c.mActive and not c._enabled)
        ''')

    def test_inventory_cleanup_uses_captured_entity_not_replaced_player_table(self):
        p=Peers('settings.statue=false')
        p.a.execute('''
        local v=dofile_once('mods/quant.ew/files/system/spectate/inventory.lua')
        local original=ctx.players.A.entity;v.acquire(original)
        ctx.players.A.entity=200;local unrelated=EntityAddComponent2(200,'InventoryGuiComponent',{mActive=false})
        v.release();assert(not entities[original].c.InventoryGuiComponent and entities[200].c.InventoryGuiComponent==unrelated)
        ''')

    def test_punctuation_hints_preserve_remaps(self):
        p=Peers('settings.statue=false')
        p.b.execute('''
        virtual_files['data/scripts/debug/keycodes.lua']='Key_COMMA = 54\\nKey_PERIOD = 55\\nKey_SLASH = 56\\nKey_Q = 20\\n'
        local s=dofile_once('mods/quant.ew/files/system/spectate/presentation.lua')
        assert(s.binding('lspectate',54)=='[,]');assert(s.binding('rspectate',55)=='[.]');assert(s.binding('sspectate',56)=='[/]')
        settings['quant.ew.rebind_lspectate']='20';assert(s.binding('lspectate',54)=='[Q]')
        ''')

    def test_late_run_end_releases_only_spectator_owned_inventory(self):
        p=Peers('settings.statue=false')
        p.tick(2)
        p.b.execute("InputIsKeyJustDown=function(k)return k==55 end")
        p.tick(1)
        p.b.execute('''
        assert(not entities[100].c.InventoryGuiComponent) -- dead view never creates a watched GUI
        ctx.run_ended=true;spectate.on_world_update_post()
        assert(not entities[100].c.InventoryGuiComponent)
        ''')

    def test_selected_entity_replacement_and_disconnect_restore_borrowed_gui(self):
        p=Peers('settings.statue=false')
        p.tick(2)
        p.b.execute("InputIsKeyJustDown=function(k)return k==55 end")
        p.tick(1)
        p.b.execute('''
        InputIsKeyJustDown=function()return false end
        entities[500]={alive=true,x=140,y=100,tags={},c={InventoryGuiComponent={mActive=false,_enabled=false}}}
        original_gui=entities[500].c.InventoryGuiComponent
        ctx.players.A.entity=500
        ''')
        p.tick(1)
        p.b.execute('''
        assert(ctx.spectating_over_peer_id=='A')
        assert(not entities[100].c.InventoryGuiComponent)
        assert(entities[500].c.InventoryGuiComponent==original_gui and not original_gui._enabled)
        ctx.players.A.dc=true
        ''')
        p.tick(1)
        p.b.execute('''
        assert(ctx.spectating_over_peer_id=='B')
        assert(entities[500].c.InventoryGuiComponent==original_gui and not original_gui._enabled)
        ''')

if __name__=='__main__':unittest.main()
