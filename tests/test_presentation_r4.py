"""Focused R4 cosmetics/lifecycle tests; native appearance evidence is separate."""
from pathlib import Path
import struct,unittest
from test_coop_revival import Peers,ROOT

class R4Tests(unittest.TestCase):
    def test_entrance_dissolve_and_new_attempt_are_separate_from_active_progress(self):
        p=Peers('settings.statue=false')
        p.a.execute('''
        local f=dofile_once('mods/quant.ew/files/system/coop_revival/visuals.lua')
        local a={{x=100,y=96,progress=.5,key='A:1'}}
        assert(#f.transition(a,0)==0 and a[1].alpha==0)
        f.transition(a,5);assert(a[1].alpha>0 and a[1].alpha<1)
        f.transition(a,11);assert(a[1].alpha==1)
        local closing=f.transition({},12)
        assert(#closing==1 and closing[1].dissolving and closing[1].progress==.5)
        closing=f.transition({},20);assert(closing[1].alpha>0 and closing[1].alpha<1)
        assert(#f.transition({},27)==0)
        f.transition(a,30);f.transition(a,41);f.transition({},42)
        local b={{x=100,y=96,progress=.01,key='A:2'}}
        assert(#f.transition(b,43)==0 and b[1].alpha==0 and b[1].progress==.01)
        f.clear();assert(#f.transition({},44)==0)
        ''')
        self.assertIsNone(p.state('.players.B.pending'))
        self.assertEqual(p.state('.players.B.run'),0)

    def test_cancellation_stops_emission_during_dissolve_and_menu_clears_it(self):
        p=Peers('settings.statue=false')
        p.a.execute('''
        local f=dofile_once('mods/quant.ew/files/system/coop_revival/visuals.lua')
        f.draw=function(g,a,b)calls.active=#a;calls.exiting=#b end
        f.accept_progress('A','B',1,.5,0,1,1);f.update({})
        frame=11;f.update({});assert(calls.active==1 and calls.exiting==0)
        f.accept_progress('A',nil,nil,nil,12,1,2)
        frame=12;f.update({});assert(calls.active==0 and calls.exiting==1)
        for e=1001,1019 do assert(not entities[e].alive)end
        ctx.is_texting=true;frame=13;f.update({})
        ctx.is_texting=false;frame=14;f.update({});assert(calls.active==0 and calls.exiting==0)
        f.accept_progress('A','B',1,.8,15,1,1);f.update({});assert(calls.active==0)
        ''')

    def test_fire_budget_lit_arc_and_reduced_effects(self):
        p=Peers('settings.statue=false')
        p.a.execute('''
        local f=dofile_once('mods/quant.ew/files/system/coop_revival/particles.lua')
        local a={{x=100,y=96,sx=108,sy=90,progress=.25,key='a',alpha=1}}
        f.update(a,1,true)
        local active,total=0,0
        for e=1001,1019 do
         local c=entities[e].c.ParticleEmitterComponent
         assert(c.count_max<=3 and c.emission_interval_min_frames>=1 and c.lifetime_max<=.42)
         if c.is_emitting then active=active+1;total=total+60/c.emission_interval_min_frames*c.count_max end
        end
        assert(active==19 and total<=1080) -- faint unfinished ink; strong completed ink
        a[1].progress=1;f.update(a,2,true);total=0
        for e=1001,1019 do local c=entities[e].c.ParticleEmitterComponent;total=total+60/c.emission_interval_min_frames*c.count_max end
        assert(total==1080)
        f.update(a,3,false);for e=1001,1019 do assert(not entities[e].alive)end
        ''')

    def test_numeric_hover_has_no_heart_or_extra_bar(self):
        p=Peers('settings.statue=false')
        p.a.execute('''
        local u=dofile_once('mods/quant.ew/files/system/player_arrows/teammates.lua')
        local o={w=427,h=240,cx=100,cy=100,scale=1,now=1,opacity=.5,mx=213,my=132}
        calls.images={};calls.ui=''
        u.draw(1,{{peer='B',x=100,y=100,hp=1.52,max_hp=8,state={}}},nil,o)
        assert(calls.ui:find('38 / 200',1,true)and #calls.images==1)
        assert(calls.images[1].width==#('38 / 200')*5+8)
        ''')

    def test_pointer_assets_are_integer_grid_directional_shapes(self):
        folder=ROOT/'quant.ew/files/system/player_arrows/pointers/eight'
        files=sorted(folder.glob('*.png'));self.assertEqual(len(files),16)
        for p in files:
            data=p.read_bytes();self.assertEqual(data[:8],b'\x89PNG\r\n\x1a\n')
            self.assertEqual(struct.unpack('>II',data[16:24]),(14,14))

    def test_color_validation_and_navigation_have_no_statue_thumbnail(self):
        p=Peers('settings.statue=false')
        p.a.execute('''
        local u=dofile_once('mods/quant.ew/files/system/player_arrows/teammates.lua')
        u.accept_color('B',0x332211);u.accept_color('B',-1);u.accept_color('B',0/0);u.accept_color('unknown',123)
        local list=u.collect(1,false)
        for _,v in ipairs(list)do if v.peer=='B'then assert(v.color[1]==0x11/255 and v.color[3]==0x33/255)end end
        local o={w=427,h=240,cx=100,cy=100,scale=1,now=1,opacity=.5}
        calls.images={}
        u.draw(1,{{peer='B',x=1000,y=100,dead=true,color={1,.3,.3},state={}}},nil,o)
        assert(#calls.images==2)
        for _,im in ipairs(calls.images)do assert(im.path:find('/pointers/eight/arrow_',1,true)and im.x==math.floor(im.x)and im.y==math.floor(im.y))end
        ''')

if __name__=='__main__':unittest.main()
