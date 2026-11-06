"""R4 eight-way assets and real presentation scheduling; native evidence separate."""
import unittest
from PIL import Image
from test_coop_revival import ROOT,Peers

class ArrowAudioTests(unittest.TestCase):
    def test_eight_shapes_are_solid_four_connected_and_exact_quarter_turns(self):
        folder=ROOT/'quant.ew/files/system/player_arrows/pointers/eight'
        masks=[]
        for i in range(8):
            a=Image.open(folder/f'arrow_{i:02d}_fill.png').getchannel('A');self.assertEqual(a.size,(14,14))
            points={(x,y)for y in range(14)for x in range(14)if a.getpixel((x,y))}
            seen={next(iter(points))};pending=list(seen)
            while pending:
                x,y=pending.pop()
                for p in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                    if p in points and p not in seen:seen.add(p);pending.append(p)
            self.assertEqual(seen,points,'No disconnected or corner-only islands')
            # Empty pixels within the bounding region must reach the outer
            # margin; no black/hollow centre in either character color.
            empty={(x,y)for y in range(14)for x in range(14)}-points
            edge={p for p in empty if p[0]in(0,13)or p[1]in(0,13)};todo=list(edge)
            while todo:
                x,y=todo.pop()
                for p in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                    if p in empty and p not in edge:edge.add(p);todo.append(p)
            self.assertEqual(edge,empty)
            masks.append(a)
        for i in range(8):self.assertEqual(masks[(i+2)%8].tobytes(),masks[i].transpose(Image.Transpose.ROTATE_270).tobytes())
        weights=[sum(v>0 for v in im.tobytes())for im in masks]
        self.assertLessEqual(max(weights)/min(weights),1.15)

    def test_renderer_shares_geometry_keeps_color_hp_and_distance(self):
        p=Peers('settings.statue=false');p.a.execute('''
        local u=dofile_once('mods/quant.ew/files/system/player_arrows/teammates.lua')
        local o={w=427,h=240,cx=0,cy=0,ox=0,oy=0,scale=1,now=0,opacity=.5}
        local directions={{570,0},{570,570},{0,570},{-570,570},{-570,0},{-570,-570},{0,-570},{570,-570}}
        for i,xy in ipairs(directions)do
         local live={peer='B',x=xy[1],y=xy[2],hp=4,max_hp=8,color={.4,.7,1},state={}}
         calls.images={};calls.ui='';u.draw(1,{live},nil,o)
         local path=calls.images[1].path
         assert(path:find(string.format('/eight/arrow_%02d_outline.png',i-1),1,true))
         assert(#calls.images==5 and calls.ui:find(' m',1,true))
         live.dead=true;calls.images={};calls.ui='';u.draw(1,{live},nil,o)
         assert(calls.images[1].path==path and #calls.images==2 and calls.ui:find(' m',1,true))
        end
        ''')

    def sound_fixture(self):
        p=Peers('settings.statue=false;settings.revive_effects=false')
        p.a.execute('''
        f=dofile_once('mods/quant.ew/files/system/coop_revival/visuals.lua')
        calls.sound_frames={};GamePlaySound=function(bank,event)
         calls.sound_frames[#calls.sound_frames+1]=frame
         assert(bank=='data/audio/Desktop/projectiles.bank'and event=='player_projectiles/summon_generic/create')
        end
        a={{x=100,y=96,key='A:1',progress=.1,charging=true}}
        function sound_at(now)frame=now;f.charging_sound(a,now)end
        ''');return p

    def test_start_repeat_and_quick_restart_each_have_their_own_clock(self):
        p=self.sound_fixture();p.a.execute('''
        for now=0,69 do sound_at(now)end
        frame=70;f.charging_sound({},70)
        a[1].key='A:2'
        for now=82,262 do sound_at(now)end
        assert(table.concat(calls.sound_frames,',')=='0,60,82,142,202,262')
        ''')

    def test_continuations_echo_duplicates_and_entity_replacement(self):
        p=self.sound_fixture();p.a.execute('''
        sound_at(10);sound_at(10);sound_at(11)
        a[2]=a[1];sound_at(12);a[2]=nil
        sound_at(70);assert(table.concat(calls.sound_frames,',')=='10,70')
        -- A real replacement can reuse the remote generation after reconnect.
        ctx.players.A.entity=999;sound_at(75)
        assert(table.concat(calls.sound_frames,',')=='10,70,75')
        ''')

    def test_late_updates_do_not_drift_phase_or_catch_up(self):
        p=self.sound_fixture();p.a.execute('''
        sound_at(0);sound_at(75);sound_at(120);sound_at(600);sound_at(600);sound_at(659);sound_at(660)
        assert(table.concat(calls.sound_frames,',')=='0,75,120,600,660')
        ''')

    def test_mute_menu_pending_and_same_attempt_resume_do_not_start_again(self):
        p=self.sound_fixture();p.a.execute('''
        sound_at(0);settings.ritual_sound=false;sound_at(30)
        settings.ritual_sound=true;sound_at(40);sound_at(60)
        ctx.is_texting=true;sound_at(100);ctx.is_texting=false;sound_at(110);sound_at(120)
        a[1].progress=1;sound_at(180);sound_at(240)
        assert(table.concat(calls.sound_frames,',')=='0,60,120')
        a[1].progress=.1;a[1].key='A:2';sound_at(245)
        assert(table.concat(calls.sound_frames,',')=='0,60,120,245')
        ''')

    def test_remote_expiry_and_cancel_tombstone_block_audio_replay(self):
        p=self.sound_fixture();p.a.execute('''
        f.accept_progress('A','B',1,.1,0,1,1);frame=0;f.update({})
        f.accept_progress('A','B',1,.2,10,1,2);frame=10;f.update({})
        frame=41;f.update({}) -- expiry
        f.accept_progress('A','B',1,.3,45,1,3);frame=45;f.update({})
        f.accept_progress('A',nil,nil,nil,46,1,4);frame=46;f.update({})
        f.accept_progress('A','B',1,.4,47,1,3);frame=47;f.update({})
        assert(table.concat(calls.sound_frames,',')=='0')
        f.accept_progress('A','B',1,.1,48,2,1);frame=48;f.update({})
        assert(table.concat(calls.sound_frames,',')=='0,48')
        ''')

if __name__=='__main__':unittest.main()
