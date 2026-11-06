"""Loose spells reuse the same conservative metadata/artwork as wand children."""
import unittest
import test_ping_spell_info as spell_fixture
import test_ping_wands as gui_fixture

class IdentitySpellTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):spell_fixture.PingSpellInfoTests.setUpClass()

 def test_all_native_backgrounds_have_full_measured_extent_and_centered_unscaled_art(self):
  gui_fixture.PingWandTests().runtime().execute('''
  local A=dofile_once('mods/quant.ew/files/system/player_ping/spell_artwork.lua')
  for kind,suffix in pairs({PROJECTILE='projectile',STATIC_PROJECTILE='static_projectile',MODIFIER='modifier',
   DRAW_MANY='draw_many',MATERIAL='material',OTHER='other',UTILITY='utility',PASSIVE='passive'})do
   local info={icon='data/ui_gfx/gun_actions/bomb.png',values={type=kind}}
   local p=A.measure(1,info);assert(p.width==20 and p.height==20 and p.background:find('item_bg_'..suffix..'.png',1,true))
   A.draw(1,100,50,info,p)
   local icon=calls.image_widgets[#calls.image_widgets];local bg=calls.image_widgets[#calls.image_widgets-1]
   assert(bg.x==100 and bg.y==50 and bg.w==20 and bg.h==20 and bg.scale==1)
   assert(icon.x==102 and icon.y==52 and icon.w==16 and icon.h==16 and icon.scale==1)
  end
  ''')

 def test_loose_spell_calls_existing_lookup_without_inventing_remaining_uses(self):
  lua=spell_fixture.PingSpellInfoTests().runtime(native=True)
  lua.execute('''
  function ModDoesFileExist()return true end
  local tr=GameTextGetTranslatedOrNot
  function GameTextGetTranslatedOrNot(k)if k=='$action_bomb'then return 'Bomb'else return tr(k)end end
  local lookup=I.lookup
  I.lookup=function(id,uses)calls.lookup_id=id;calls.lookup_uses=uses;local v=lookup(id,uses);calls.lookup_info=v;return v end
  local m={key='x',sender='A',core_box={x=100,y=70,w=7,h=7},record={seq=1,kind='spell',ident={v=1,category='spell',id='BOMB'}}}
  local o={now=1,w=427,h=240,mx=103,my=73,body=1};P.clear();P.draw(1,{m},o);o.now=13
  local b=assert(P.draw(1,{m},o));assert(calls.lookup_id=='BOMB'and calls.lookup_uses==nil)
  local loose=calls.lookup_info;assert(loose.values.uses==nil and loose.values.explosion_damage==125)
  for _,row in ipairs(loose.rows)do assert(row.key~='uses')end
  local child=lookup('BOMB',2);assert(child.values.uses==2)
  for k,v in pairs(loose.display)do assert(child.display[k]==v)end
  local p=T.measure(1,child,{max_width=419,max_height=232});assert(p.artwork.width==20 and p.artwork.height==20)
  assert(T.draw(1,{x=50,y=50,w=7,h=7},child,{w=427,h=240,plan=p}))
  ''')

 def test_bordered_icon_padding_native_format_and_tiny_wand_speed_unchanged(self):
  lua=spell_fixture.PingSpellInfoTests().runtime(native=True)
  lua.execute('''
  local v=I.lookup('SPITTER');assert(v.values.projectile_damage==7.5 and v.display.projectile_damage=='8')
  local p=assert(T.measure(1,v,{max_width=232,max_height=232}));assert(p.icon_w==20 and p.icon_h==20)
  local b=assert(T.draw(1,{x=220,y=235,w=7,h=7},v,{plan=p,w=240,h=240}))
  assert(b.x>=4 and b.y>=4 and b.x+b.w<=236 and b.y+b.h<=236)
  for _,a in ipairs(calls.image_widgets)do
   if a.path:find('item_bg_',1,true)then assert(a.x>=b.x+5 and a.y>=b.y+5 and a.x+a.w<=b.x+b.w-5)end
  end
  assert(E.FormatSpeedMultiplier(.000001)=='1e-06x')
  ''')

if __name__=='__main__':unittest.main()
