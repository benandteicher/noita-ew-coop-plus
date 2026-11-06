"""Continuous ping projection and fresh, pure core selection; no native claims."""
import math
import unittest
from pathlib import Path

from lupa.luajit21 import LuaRuntime
from test_ping_renderer_r2 import runtime

ROOT = Path(__file__).resolve().parents[1]


class PingProjectionR9Tests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.projection = self.lua.execute((ROOT / 'quant.ew/files/system/player_ping/projection.lua').read_text())
        self.view = self.lua.table_from(dict(camera_x=0, camera_y=0, world_w=400, world_h=240, w=400, h=240))

    def project(self, x, y):
        return self.projection.project(x, y, self.view)

    def test_true_viewport_classification_is_separate_from_safe_inset(self):
        for x, outside, pinned, px in ((0, False, False, 200), (185, False, False, 385),
                                      (186, False, True, 386), (190, False, True, 386),
                                      (200, False, True, 386), (201, True, True, 386)):
            p = self.project(x, 0)
            self.assertEqual((p.x, p.y, p.outside, p.edge_pinned), (px, 120, outside, pinned))

    def test_crossing_inset_and_true_edges_has_no_position_jump(self):
        for sx, sy in ((1, 0), (0, 1), (1, 1), (-1, 1), (-1, -1), (1, -1)):
            previous = None
            for step in range(8001):
                t = step / 20
                p = self.project(sx * t, sy * t)
                self.assertGreaterEqual(p.x, 14)
                self.assertLessEqual(p.x, 386)
                self.assertGreaterEqual(p.y, 14)
                self.assertLessEqual(p.y, 226)
                if previous:
                    self.assertLessEqual(math.hypot(p.x - previous.x, p.y - previous.y), .072)
                previous = p

    def test_diagonal_and_corner_rays_preserve_aspect_and_fractional_coordinates(self):
        p = self.project(1000, 1000)
        self.assertAlmostEqual(p.x, 306)
        self.assertEqual(p.y, 226)
        self.assertAlmostEqual((p.x - 200) / (p.y - 120), 1)
        p = self.project(1860, 1060)
        self.assertEqual((p.x, p.y), (386, 226))
        self.view.camera_x, self.view.camera_y = 100, 200
        self.view.world_w, self.view.world_h = 800, 400
        p = self.project(433.25, 231.75)
        self.assertAlmostEqual(p.x, 366.625)
        self.assertAlmostEqual(p.y, 139.05)
        self.assertFalse(p.edge_pinned)

    def test_origin_and_unrelated_hud_information_do_not_move_projection(self):
        p = self.project(-500, 30)
        self.view.origin_x, self.view.origin_y = 99999, -99999
        self.view.reserved = self.lua.table_from([self.lua.table_from(dict(x=0, y=0, w=400, h=240))])
        q = self.project(-500, 30)
        self.assertEqual((p.x, p.y), (q.x, q.y))

    def test_invalid_and_degenerate_views_fail_closed_without_mutation(self):
        for field in ('camera_x', 'camera_y', 'world_w', 'world_h', 'w', 'h'):
            old = self.view[field]
            for invalid in (None, float('nan'), float('inf'), '4'):
                self.view[field] = invalid
                self.assertIsNone(self.project(1, 2), (field, invalid))
            self.view[field] = old
        for field in ('world_w', 'world_h', 'w', 'h'):
            old = self.view[field]
            for invalid in (0, -1):
                self.view[field] = invalid
                self.assertIsNone(self.project(1, 2))
            self.view[field] = old
        for x, y in ((None, 0), (0, float('nan')), (float('inf'), 0)):
            self.assertIsNone(self.project(x, y))
        self.assertEqual(len(list(self.view.items())), 6)
        self.assertEqual((self.project(0, 0).x, self.project(0, 0).y), (200, 120))


class PingCoreSelectionR9Tests(unittest.TestCase):
    def test_session_lifetime_is_visible_while_invalid_or_elapsed_expiry_is_rejected(self):
        runtime().execute('''
        record.expires=math.huge;local m=draw(1000000)
        assert(m and m.alpha==.65)
        opts.mx=m.center_x;opts.my=m.center_y
        assert(renderer.hit(records,opts).key==record.key)
        for _,expiry in ipairs({-math.huge,0/0,-1,1000000})do
          record.expires=expiry;assert(not draw(1000000))
        end
        ''')

    def test_layout_and_hit_need_no_gui_and_have_no_animation_side_effects(self):
        runtime().execute('''
        local first=draw(200)
        local image,text,measure=GuiImage,GuiText,GuiGetTextDimensions
        GuiImage=function()error('pure path drew')end;GuiText=GuiImage;GuiGetTextDimensions=GuiImage
        opts.mx=200;opts.my=120
        local hit,list=renderer.hit(records,opts)
        assert(hit.key==record.key and list.hover_key==record.key and hit.hovered)
        assert(hit.center_x==first.center_x and samebox(hit,first))
        GuiImage,GuiText,GuiGetTextDimensions=image,text,measure
        opts.mx=nil;opts.my=nil;opts.idle_opacity=.25
        assert(draw(201).alpha==first.alpha) -- hit did not advance draw state
        assert(draw(210).alpha==.25)
        ''')

    def test_closest_core_retention_recent_event_and_key_ties_ignore_record_order(self):
        runtime().execute('''
        records={
          {key='Z',sender='Z',id=1,updated=10,kind='wand',x=0,y=0,created=0,expires=1000},
          {key='B',sender='B',id=2,updated=20,kind='wand',x=4,y=0,created=0,expires=1000},
          {key='A',sender='A',id=3,updated=20,kind='wand',x=4,y=0,created=0,expires=1000}}
        opts.now=200;opts.mx=203;opts.my=120
        assert(renderer.hit(records,opts).key=='A') -- closest; updated and ID tie-breaks
        records[3].id=2;assert(renderer.hit(records,opts).key=='A') -- stable key
        records[3].updated=19;assert(renderer.hit(records,opts).key=='B')
        opts.selected_key='Z';assert(renderer.hit(records,opts).key=='Z') -- retained only while eligible
        opts.mx=210;assert(renderer.hit(records,opts).key=='B') -- Z no longer eligible
        opts.selected_key=nil;opts.mx=201;assert(renderer.hit(records,opts).key=='Z') -- closest wins over newest
        records[1],records[3]=records[3],records[1]
        assert(renderer.hit(records,opts).key=='Z')
        ''')

    def test_fresh_pointer_camera_expiry_and_core_margin_match_draw(self):
        runtime().execute('''
        opts.now=200;opts.mx=200;opts.my=120
        local old=draw(200);assert(hover==record.key)
        opts.view.camera_x=30
        assert(not renderer.hit(records,opts)) -- current projection, never last draw geometry
        opts.mx=170
        local hit,list=renderer.hit(records,opts);local drawn=draw(200)
        assert(hit.key==hover and list.hover_key==markers.hover_key and samebox(hit,drawn))
        opts.mx=drawn.core_box.x-3;assert(renderer.hit(records,opts))
        opts.mx=drawn.core_box.x-3.001;assert(not renderer.hit(records,opts))
        opts.mx=drawn.center_x;opts.my=drawn.center_y-20;assert(not renderer.hit(records,opts))
        opts.my=drawn.center_y;opts.now=1000;assert(not renderer.hit(records,opts))
        ''')

    def test_names_hud_waves_and_all_accepted_collisions_never_move_or_omit_cores(self):
        runtime().execute('''
        opts.now=200;records={}
        for sender=1,128 do for event=1,3 do
          local key=string.format('%03d:%d',sender,event)
          records[#records+1]={key=key,sender=tostring(sender),kind='wand',x=10000,y=-10000,created=0,expires=1000}
        end end
        local plain=renderer.layout(records,opts);assert(#plain==384)
        opts.names={};opts.reserved={{x=0,y=0,w=400,h=240}}
        draw(201);assert(#markers==384 and #texts==0)
        local fills=0
        for _,i in ipairs(images)do if i.path:find('clipped_fill')then fills=fills+1 end end
        assert(fills==384)
        for i,m in ipairs(markers)do assert(samebox(m,plain[i]))end
        ''')

    def test_wave_and_conditional_labels_cannot_remap_other_widget_identities(self):
        runtime().execute('''
        local a=draw(35);local ids={}
        for _,i in ipairs(images)do ids[i.path]={id=i.id,scope=i.scope}end
        local name_scope=texts[2].scope
        draw(36)
        for _,i in ipairs(images)do assert(ids[i.path].id==i.id and ids[i.path].scope==i.scope)end
        assert(texts[2].scope==name_scope)
        records[2]={key='0:new',sender='B',kind='danger',x=-60,y=0,created=0,expires=1000}
        draw(90) -- both owner labels become hidden; existing core IDs remain stable
        for _,i in ipairs(images)do if i.scope=='ew_ping:'..record.key then
          assert(ids[i.path].id==i.id and ids[i.path].scope==i.scope)
        end end
        for _,i in ipairs(images)do assert(i.id>=1 and i.id<=3 and i.x%1==0 and i.y%1==0)end
        opts.selected_key=record.key;draw(100)
        local found=false
        for _,t in ipairs(texts)do
          assert(t.x%1==0 and t.y%1==0)
          if t.value=='You'then found=true;assert(t.scope==name_scope)end
        end
        assert(found)
        ''')

    def test_selected_core_and_its_labels_draw_last_at_overlaps(self):
        runtime().execute('''
        records={
          {key='A',sender='A',id=2,updated=20,kind='wand',x=0,y=0,created=0,expires=1000},
          {key='Z',sender='B',id=1,updated=10,kind='danger',x=0,y=0,created=0,expires=1000}}
        opts.mx=200;opts.my=120;draw(0)
        assert(markers.hover_key=='A' and images[#images].scope=='ew_ping:A')
        assert(images[#images].path:find('clipped_fill') and texts[#texts].scope=='ew_ping:A:name')
        opts.selected_key='Z';draw(1)
        assert(markers.hover_key=='Z' and images[#images].scope=='ew_ping:Z')
        assert(texts[#texts].scope=='ew_ping:Z:name')
        ''')

    def test_fractional_text_metrics_keep_integer_shadow_and_viewport_bounds(self):
        runtime().execute('''
        function GuiGetTextDimensions(_,value)return #value*4.35,8.5 end
        record.x=10000;record.y=10000;opts.selected_key=record.key
        draw(200)
        for _,t in ipairs(texts)do
          local width,height=GuiGetTextDimensions(1,t.value)
          assert(t.x%1==0 and t.y%1==0 and t.x>=2 and t.y>=2)
          assert(t.x+width<=opts.view.w-1 and t.y+height<=opts.view.h-1)
        end
        for _,box in pairs(markers[1].label_boxes)do
          assert(box.x%1==0 and box.y%1==0 and box.w%1==0 and box.h%1==0)
          assert(box.x+box.w<=opts.view.w-2 and box.y+box.h<=opts.view.h-2)
        end
        ''')

    def test_hovered_edge_owner_and_shared_qualifier_fit_without_moving_core(self):
        runtime().execute('''
        record.kind='wand';record.source='held'
        opts.now=200;opts.reserved={{x=0,y=0,w=400,h=240}}
        for _,point in ipairs({{0,-10000},{0,10000},{-10000,0},{10000,0},
            {-10000,-10000},{10000,-10000},{-10000,10000},{10000,10000}})do
          record.x,record.y=point[1],point[2]
          local projected=renderer.layout(records,opts)[1]
          opts.mx=projected.center_x;opts.my=projected.center_y
          local m=draw(200)
          assert(samebox(projected,m) and m.hovered and m.name_visible and m.source_visible and m.distance_visible)
          assert(not overlaps(m.label_boxes.name,m.label_boxes.source))
          assert(not overlaps(m.label_boxes.name,m.label_boxes.distance))
          assert(not overlaps(m.label_boxes.source,m.label_boxes.distance))
          for _,box in pairs(m.label_boxes)do
            assert(box.x>=2 and box.y>=2 and box.x+box.w<=398 and box.y+box.h<=238)
            assert(not overlaps(box,m.core_box))
          end
        end
        record.source=nil;record.x=0;record.y=10000;opts.mx=200;opts.my=226
        local m=draw(200)
        assert(m.name_visible and m.distance_visible and m.label_boxes.name.y<m.label_boxes.distance.y)
        ''')

    def test_edge_distance_exit_fade_and_idle_labels_do_not_follow_wave_alpha(self):
        runtime().execute('''
        record.x=190;local a=draw(200)
        assert(a.edge_pinned and not a.outside and a.distance_visible)
        local distance_alpha=texts[2].color[4]
        local b=draw(240);assert(b.distance_visible and texts[2].color[4]==distance_alpha)
        assert(b.wave_size and not a.wave_size and b.label_alpha==a.label_alpha)
        record.x=0;local c=draw(241)
        assert(not c.edge_pinned and c.distance_visible and c.center_x==200)
        assert(texts[2].color[4]<distance_alpha)
        draw(246);assert(texts[2].color[4]>0 and texts[2].color[4]<distance_alpha)
        assert(not draw(252).distance_visible)
        renderer.clear();assert(not draw(253).distance_visible) -- no distance on fresh interior core
        ''')


if __name__ == '__main__':
    unittest.main()
