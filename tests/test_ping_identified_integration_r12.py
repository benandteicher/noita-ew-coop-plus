"""R12 adapter wiring/receiver presentation. Native targeting is separate evidence."""
import unittest
import test_ping_integration as integration
import test_ping_wands as wands

SOURCE='''
local original_source=ModTextFileGetContent
function ModTextFileGetContent(p)if p~='data/scripts/gun/gun_actions.lua'then return original_source and original_source(p)or''end;return [=[actions={
 {
 id="BOMB",name="$action_bomb",description="$actiondesc_bomb",sprite="data/ui_gfx/gun_actions/bomb.png",action=function()end},
 }]=]end
function ModDoesFileExist()return true end
local translate=GameTextGetTranslatedOrNot
function GameTextGetTranslatedOrNot(k)
 if k=='$action_bomb'then return 'Bomb'elseif k=='$actiondesc_bomb'then return 'An explosive bomb.'end
 return translate(k)
end
'''

class IdentifiedIntegrationTests(unittest.TestCase):
 def test_final_capture_uses_current_view_without_retargeting_queued_world_intent(self):
  h=integration.PingIntegrationTests();p=h.peers(SOURCE+'''
  local T=dofile_once('mods/quant.ew/files/system/player_ping/world_target.lua')
  T.target=function(x,y,v)last_target_view=v.camera_x;last_target_x=x;return nil end
  ''')
  p.a.execute('frame=frame+1;ping_pressed=true;ping.on_world_update();GameGetCameraPos=function()return 1000,0 end;ping.on_world_update_post()');p.flush()
  self.assertEqual(p.a.eval('last_target_view'),1000)
  self.assertEqual(p.a.eval('last_target_x'),20)
  self.assertEqual(p.b.eval('ping_records()[1].kind'),'location')

 def test_two_peer_capture_refresh_upgrade_cancel_and_unknown_fallback(self):
  h=integration.PingIntegrationTests();p=h.peers(SOURCE+'''
  ident_target=50
  local T=dofile_once('mods/quant.ew/files/system/player_ping/world_target.lua')
  T.target=function(x,y,v)if ident_target then return {entity=ident_target,x=x,y=y,kind='spell',ident={v=1,category='spell',id='BOMB'}}end end
  T.revalidate=function(s,x,y,v)if s.entity==ident_target then return s end end
  ''')
  h.press(p)
  for l in p.peers.values():
   self.assertEqual(l.eval('ping_records()[1].ident.id'),'BOMB')
   self.assertEqual(l.eval('ping_records()[1].kind'),'spell')
  h.step(p,4);h.press(p)
  for l in p.peers.values():
   self.assertEqual(l.eval('#ping_records()'),1)
   self.assertEqual(l.eval('ping_records()[1].kind'),'danger')
   self.assertEqual(l.eval('ping_records()[1].ident.id'),'BOMB')
  h.step(p,4);p.a.execute('cursor_x=29');h.press(p)
  self.assertEqual(p.b.eval('#ping_records()'),1)
  self.assertEqual(p.b.eval('ping_records()[1].kind'),'danger')
  h.step(p,25);p.a.execute('cursor_x=30');h.press(p)
  self.assertEqual(p.b.eval('#ping_records()'),1)
  self.assertEqual(p.b.eval('ping_records()[1].kind'),'spell')
  h.step(p,25);p.a.execute('ident_target=51;cursor_x=40');h.press(p)
  self.assertEqual(p.b.eval('#ping_records()'),2)
  p.a.execute('ident_target=nil;cursor_x=100');h.step(p,25);h.press(p)
  self.assertEqual(p.b.eval('ping_records()[3].kind'),'location')
  p.a.execute("local r=ping_records()[1];receive('A','cancel',encode({ping_state().run,r.epoch,r.id}))")
  self.assertEqual(p.a.eval('#ping_records()'),2)

 def test_wire_bad_known_missing_art_metadata_has_safe_location_fallback(self):
  h=integration.PingIntegrationTests();p=h.peers(SOURCE)
  for ident in ("{v=1,category='spell',id='UNSUPPORTED'}", "{v=1,category='item',id='potion',icon='evil.png'}"):
   p.a.execute("receive('B','ping',encode({{v=1,run=ping_state().run,epoch=1,id="+str(10+len(ident))+",seq=1,kind='item',x=0,y=0,ident="+ident+"}}))")
  self.assertEqual(p.a.eval('#ping_records()'),2)
  self.assertTrue(p.a.eval("ping_records()[1].kind=='location'and ping_records()[2].kind=='location'"))

 def test_simple_receiver_hover_only_exit_removal_body_and_menu_clear(self):
  l=wands.PingWandTests().runtime();l.execute(SOURCE+'''
  P.clear();m={key='A:1',sender='A',core_box={x=100,y=70,w=7,h=7},record={seq=1,kind='spell',ident={v=1,category='spell',id='BOMB'}}}
  markers={m};o={now=0,w=427,h=240,mx=8,my=8,body=1}
  assert(P.draw(1,markers,o)==nil)
  o.mx=103;o.my=73;o.now=1;assert(P.draw(1,markers,o)==nil)
  o.now=13;card=assert(P.draw(1,markers,o));assert(card.x>=4 and card.y>=4 and card.x+card.w<=423 and card.y+card.h<=236)
  assert(P.last_bounds().child==nil)
  o.now=14;o.mx=card.x+card.w/2;o.my=card.y+card.h/2;assert(P.draw(1,markers,o))
  o.mx=2;o.my=238;o.now=15;assert(P.draw(1,markers,o))
  o.now=32;assert(P.draw(1,markers,o)==nil)
  o.mx=103;o.my=73;o.now=33;P.draw(1,markers,o);o.now=45;assert(P.draw(1,markers,o))
  assert(P.draw(1,{},o)==nil);o.now=46;P.draw(1,markers,o);o.now=58;assert(P.draw(1,markers,o))
  o.hidden=true;assert(P.draw(1,markers,o)==nil);o.hidden=false;o.now=59;P.draw(1,markers,o)
  o.body=2;o.now=71;assert(P.draw(1,markers,o)==nil)
  ''')

 def test_item_enemy_and_animated_refresher_actual_bounds_at_corners(self):
  l=wands.PingWandTests().runtime();l.execute(SOURCE+'''
  GUI_RECT_ANIMATION_PLAYBACK={Loop=2}
  local draw=GuiImage
  function GuiImage(g,id,x,y,path,a,sx,sy,rot,play,animation)
   if path=='data/items_gfx/spell_refresh.xml'then assert(play==2 and animation=='default');calls.refresher={x=x,y=y} end
   return draw(g,id,x,y,path,a,sx,sy,rot)
  end
  for _,spec in ipairs({{'item','potion'},{'item','spell_refresh'},{'enemy','zombie'}})do
   for _,xy in ipairs({{4,4},{205,4},{4,135},{205,135}})do
    P.clear();local m={key='x',sender='A',core_box={x=xy[1],y=xy[2],w=7,h=7},record={seq=1,kind=spec[1],ident={v=1,category=spec[1],id=spec[2]}}}
    local o={now=1,w=220,h=150,mx=xy[1]+3,my=xy[2]+3};P.draw(1,{m},o);o.now=13
    local b=assert(P.draw(1,{m},o));assert(b.x>=4 and b.y>=4 and b.x+b.w<=216 and b.y+b.h<=146)
    if spec[2]=='spell_refresh'then
     local x,y=calls.refresher.x-9,calls.refresher.y-20
     assert(x>=b.x+5 and y>=b.y+5 and x+20<=b.x+b.w-5 and y+20<=b.y+b.h-5)
    end
    if spec[1]=='enemy'then local found=false;for _,t in ipairs(calls.text)do if t.text=='Spotted here'then found=true end end;assert(found)end
   end
  end
  assert(calls.refresher)
  ''')

if __name__=='__main__':unittest.main()
