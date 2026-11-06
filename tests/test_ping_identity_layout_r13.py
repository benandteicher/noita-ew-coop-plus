"""Measured compact identity cards; actual renderer evidence is coordinator-owned."""
import unittest
import test_ping_wands as fixture


class IdentityLayoutTests(unittest.TestCase):
    def runtime(self, inset=0):
        lua=fixture.PingWandTests().runtime()
        lua.globals().FRAME_INSET=inset
        lua.execute('''
        GUI_RECT_ANIMATION_PLAYBACK={Loop=2}
        I=dofile_once('mods/quant.ew/files/system/player_ping/identity_tooltip.lua')
        function identity(name,w,h,category)
          return{name=name,icon='data/native_icon.png',icon_w=w or 16,icon_h=h or 16,category=category or 'item'}
        end
        ''')
        return lua

    def test_short_names_actual_width_centering_title_gap_and_bottom_clearance(self):
        for inset in (0,3):
            lua=self.runtime(inset)
            lua.execute('''
            local data=identity('Potion');local p=assert(I.measure(1,data,{max_width=240,max_height=232}))
            assert(p.content==24 and p.width==34+FRAME_INSET*2 and p.width<100)
            assert(p.icon_y==13 and p.height==39+FRAME_INSET*2)
            local b=assert(I.draw(1,{x=100,y=70,w=7,h=7},data,{plan=p,w=427,h=240}))
            local t=calls.text[#calls.text];local a=calls.image_widgets[#calls.image_widgets]
            assert(math.abs((t.x+12)-(b.x+b.w/2))<=.5)
            assert(math.abs((a.x+8)-(b.x+b.w/2))<=.5)
            assert(a.y-(t.y+9)==4)
            assert(b.y+b.h-(a.y+16)==5+FRAME_INSET)
            ''')

    def test_portrait_caption_has_measured_spacing_and_items_ignore_stats_description(self):
        self.runtime(3).execute('''
        local item=identity('HP',16,16);item.description='Never shown';item.rows={{label='No stats',value='0'}}
        local p=assert(I.measure(1,item));I.draw(1,{x=50,y=50,w=7,h=7},item,{plan=p})
        assert(#calls.text==1 and calls.text[1].text=='HP'and p.caption==nil)
        calls.text={};calls.image_widgets={}
        local enemy=identity('Hurtta',16,16,'enemy');p=assert(I.measure(1,enemy));local b=I.draw(1,{x=50,y=50,w=7,h=7},enemy,{plan=p})
        assert(p.content==48 and p.caption=='Spotted here')
        local art=calls.image_widgets[#calls.image_widgets];local caption=calls.text[2]
        assert(caption.text=='Spotted here'and caption.y-art.y-16==4)
        assert(b.y+b.h-caption.y-9==8)
        ''')

    def test_long_names_wrap_utf8_without_font_scaling_and_card_stays_bounded(self):
        self.runtime(3).execute('''
        local long=identity('An unusually long potion identification name with more words than fit comfortably')
        local p=assert(I.measure(1,long,{max_width=180,max_height=130}))
        assert(#p.title_lines==3 and p.width<=176 and p.height<=130)
        for _,line in ipairs(p.title_lines)do assert(GuiGetTextDimensions(1,line)<=160)end
        local unicode=identity(string.rep('Hämis',30));local q=assert(I.measure(1,unicode,{max_width=100,max_height=130}))
        assert(#q.title_lines==3 and q.title_lines[3]:sub(-3)=='...')
        for _,line in ipairs(q.title_lines)do
          assert(not line:find('[\\194-\\244]%.%.%.$'))
        end
        for _,xy in ipairs({{-100,-100},{420,230},{4,230},{420,4}})do
          local b=assert(I.draw(1,{x=100,y=80,w=7,h=7},long,{plan=p,w=220,h=150,box={x=xy[1],y=xy[2]}}))
          assert(b.x>=4 and b.y>=4 and b.x+b.w<=216 and b.y+b.h<=146)
        end
        for _,t in ipairs(calls.text)do assert(t.scale==1)end
        ''')

    def test_native_animation_origin_playback_dimensions_and_frozen_origin(self):
        self.runtime(3).execute('''
        local data=identity('Refresher',20,20)
        data.icon='data/items_gfx/spell_refresh.xml';data.icon_animation='default';data.icon_offset_x=9;data.icon_offset_y=20
        local original=GuiImage
        function GuiImage(g,id,x,y,path,a,sx,sy,rot,play,animation)
          if path==data.icon then assert(play==2 and animation=='default'and sx==1 and sy==1);last={x=x,y=y}end
          return original(g,id,x,y,path,a,sx,sy,rot)
        end
        local p=assert(I.measure(1,data));local b=assert(I.draw(1,{x=50,y=50,w=7,h=7},data,{plan=p,box={x=150,y=90}}))
        assert(last.x-9==math.floor(b.x+(b.w-20)/2+.5)and last.y-20==b.y+8+p.icon_y)
        local c=I.draw(1,{x=200,y=200,w=7,h=7},data,{plan=p,box=b})
        assert(c.x==b.x and c.y==b.y and c.w==b.w and c.h==b.h)
        ''')

    def test_missing_art_and_impossibly_small_viewports_fail_without_drawing(self):
        self.runtime().execute('''
        assert(I.measure(1,{name='No art'})==nil)
        assert(I.measure(1,identity('Large',64,64),{max_width=50,max_height=100})==nil)
        assert(I.measure(1,identity('Tall',16,64),{max_width=100,max_height=40})==nil)
        assert(I.draw(1,{x=0,y=0,w=7,h=7},identity('Tall',16,64),{w=80,h=40})==nil)
        ''')


if __name__=='__main__':unittest.main()
