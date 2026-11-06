"""Passive preview travel/exit contracts against production GUI renderer adapters."""
import unittest
import test_ping_wands as fixture

class HoverTravelTests(unittest.TestCase):
 def runtime(self):return fixture.PingWandTests().runtime()
 def test_broad_bridges_both_sides_and_overshoot_grace(self):
  self.runtime().execute('''
  local G=dofile_once('mods/quant.ew/files/system/player_ping/hover_geometry.lua')
  local dot={x=200,y=100,w=7,h=7}
  assert(G.bridge(225,140,dot,{x=240,y=60,w=100,h=130}))
  assert(G.bridge(175,140,dot,{x=60,y=60,w=100,h=130}))
  assert(not G.bridge(20,220,dot,{x=240,y=60,w=100,h=130}))
  local s={};assert(G.retain(s,true,0));assert(G.retain(s,false,1))
  assert(G.retain(s,false,15));assert(not G.retain(s,false,17))
  assert(G.retain(s,true,18));assert(s.left_at==nil)
  ''')
 def test_parent_and_child_survive_curved_travel_then_close_after_grace(self):
  self.runtime().execute('''
  local marker={key='travel',sender='A',record={seq=1,wand=sample(8)},core_box={x=200,y=100,w=7,h=7}}
  local o={now=0,w=640,h=360,mx=203,my=103,body=1}
  P.draw(1,{marker},o);o.now=12;local b=assert(P.draw(1,{marker},o))
  local x,y=b.x,b.y
  o.now=13;o.mx=(207+b.x)/2;o.my=b.y+b.h*.7
  assert(P.draw(1,{marker},o));assert(P.last_bounds().actual.x==x and P.last_bounds().actual.y==y)
  o.now=14;o.mx=b.x+b.w+9;o.my=b.y+b.h/2;assert(P.draw(1,{marker},o))
  o.now=20;o.mx=b.x+b.w+4;assert(P.draw(1,{marker},o))
  o.now=21;o.mx=2;o.my=2;assert(P.draw(1,{marker},o))
  o.now=37;assert(not P.draw(1,{marker},o));assert(not P.active_key())
  ''')
 def test_actual_record_removal_does_not_wait_for_exit_grace(self):
  self.runtime().execute('''
  local m={key='gone',sender='A',record={seq=1,wand=sample(8)},core_box={x=100,y=100,w=7,h=7}}
  local o={now=0,w=640,h=360,mx=103,my=103,body=1}
  P.draw(1,{m},o);o.now=12;local b=assert(P.draw(1,{m},o))
  o.now=13;o.mx=b.x+10;o.my=b.y+10
  assert(not P.draw(1,{},o));assert(not P.active_key())
  ''')
 def test_different_selected_core_overrides_old_bridge_retention(self):
  self.runtime().execute('''
  local a={key='A',sender='A',record={seq=1,wand=sample(8)},core_box={x=100,y=100,w=7,h=7}}
  local b={key='B',sender='B',record={seq=1,wand=sample(8)},core_box={x=111,y=110,w=7,h=7}}
  local o={now=0,w=640,h=360,mx=103,my=103,body=1}
  local markers={a,b};markers.hover_key='A';a.hovered=true;b.hovered=false
  P.draw(1,markers,o);o.now=12;assert(P.draw(1,markers,o));assert(P.active_key()=='A')
  markers.hover_key='B';a.hovered=false;b.hovered=true;o.mx=114;o.my=113;o.now=13
  assert(not P.draw(1,markers,o));o.now=25;assert(P.draw(1,markers,o));assert(P.active_key()=='B')
  local box=P.last_bounds().actual
  assert(P.blocks_world(box.x+10,box.y+10));assert(not P.blocks_world(2,2))
  ''')
 def test_dot_hidden_under_real_card_cannot_steal_hover(self):
  self.runtime().execute('''
  local a={key='A',sender='A',record={seq=1,wand=sample(8)},core_box={x=100,y=100,w=7,h=7}}
  local o={now=0,w=640,h=360,mx=103,my=103,body=1}
  P.draw(1,{a},o);o.now=12;local box=assert(P.draw(1,{a},o))
  local b={key='B',sender='B',record={seq=1,wand=sample(8)},core_box={x=box.x+10,y=box.y+10,w=7,h=7}}
  local markers={a,b};markers.hover_key='B';a.hovered=false;b.hovered=true
  o.now=13;o.mx=box.x+13;o.my=box.y+13
  assert(P.draw(1,markers,o));assert(P.active_key()=='A')
  ''')
