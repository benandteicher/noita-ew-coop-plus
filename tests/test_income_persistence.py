"""Production v2 paged persistence through LuaJIT; no native save claim."""
from pathlib import Path
import unittest
from lupa.luajit21 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


class PersistenceTests(unittest.TestCase):
    def runtime(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().READ = lambda p: (ROOT / p.removeprefix('mods/')).read_text(encoding='utf-8')
        lua.execute('''
        globals={}; writes={}; cache={}
        function GlobalsGetValue(k,d)return globals[k]or d end
        function GlobalsSetValue(k,v)globals[k]=v;writes[#writes+1]={key=k,bytes=#v}end
        function dofile_once(p)
            if cache[p]then return cache[p]end
            local v=assert(loadstring(READ(p)))();cache[p]=v;return v
        end
        store=dofile_once('mods/quant.ew/files/system/shared_income/persistence.lua')
        function fresh_store()
            return assert(loadstring(READ('mods/quant.ew/files/system/shared_income/persistence.lua')))()
        end
        ''')
        return lua

    def test_identity_history_roundtrip_touches_only_one_page(self):
        lua = self.runtime()
        lua.execute('''
        s={version=2,run='test',accounts={},claims={},chests={}}
        for n=1,10000 do s.claims[tostring(n)]=true end
        store.write('run',s);writes={}
        s.claims['10001']=true;store.touch(s.claims,'10001');store.write('run',s)
        assert(#writes==2 and writes[1].bytes<4096)
        restored=fresh_store().read('run')
        for n=1,10001 do assert(restored.claims[tostring(n)])end
        assert(restored.run=='test')
        ''')

    def test_sequence_replay_and_pending_compaction_do_not_resurrect(self):
        lua = self.runtime()
        lua.execute('''
        s={version=2,run='test',accounts={A={pending={},history={}}},claims={},chests={}}
        local a=s.accounts.A
        for n=1,3200 do a.history[n]={seq=n,amount=10};a.pending[n]=a.history[n]end
        store.write('run',s)
        for n=1,3072 do
            a.pending[n]=nil;store.touch(a.pending,n)
            if n<=1024 then a.history[n]=nil;store.touch(a.history,n)end
        end
        store.write('run',s);writes={}
        a.history[3201]={seq=3201,amount=20};store.touch(a.history,3201)
        a.pending[3201]=a.history[3201];store.touch(a.pending,3201)
        -- Metadata may be populated after transaction enrollment.
        a.pending[3201].name='Collector'
        store.write('run',s);assert(#writes==3)
        local r=fresh_store().read('run').accounts.A
        for n=1,3201 do
            assert((r.pending[n]~=nil)==(n>3072))
            assert((r.history[n]~=nil)==(n>1024))
        end
        assert(r.history[3201].name=='Collector'and r.pending[3201].amount==20)
        ''')

    def test_owner_and_peer_prefixes_are_independent(self):
        lua = self.runtime()
        lua.execute('''
        local s={version=2,claims={},accounts={A={pending={[1]={amount=10}},history={}},B={pending={[1]={amount=20}},history={}}}}
        store.write('run',s)
        local o={version=2,applied=100,retired={['12']=true},claims={},effects={}}
        store.write('owner',o)
        local r=fresh_store().read('run');local p=fresh_store().read('owner')
        assert(r.accounts.A.pending[1].amount==10 and r.accounts.B.pending[1].amount==20)
        assert(p.retired['12']and p.applied==100)
        ''')

    def test_missing_or_misfiled_referenced_page_fails_closed(self):
        lua = self.runtime()
        lua.execute('''
        local s={version=2,retired={['12']=true}}
        store.write('owner',s)
        local key;for k in pairs(globals)do if k~='owner'then key=k end end
        local saved=globals[key];globals[key]=nil
        assert(not pcall(function()fresh_store().read('owner')end))
        local b=dofile_once('mods/quant.ew/files/lib/bitser.lua')
        local base=dofile_once('mods/quant.ew/files/resource/base64.lua')
        globals[key]=base.encode(b.dumps({['13']=true}))
        assert(not pcall(function()fresh_store().read('owner')end))
        globals[key]=saved;assert(fresh_store().read('owner').retired['12'])
        ''')

    def test_v1_remains_single_blob_and_preserves_full_state(self):
        lua = self.runtime()
        lua.execute('''
        local v={version=1,claims={['10']=true},accounts={A={pending={[1]={amount=7}}}}}
        store.write('legacy',v);assert(#writes==1 and writes[1].key=='legacy')
        local r=fresh_store().read('legacy')
        assert(r.claims['10']and r.accounts.A.pending[1].amount==7)
        ''')

    def test_ring_reuse_preserves_both_partial_boundaries_and_rejects_stale_root(self):
        lua = self.runtime()
        lua.execute('''
        local a={pending={},history={}}
        local s={version=2,claims={},accounts={A=a}}
        for n=2,65537 do a.history[n]={seq=n,amount=10}end
        store.write('run',s);local oldroot=globals.run
        for n=2,129 do a.history[n]=nil;store.touch(a.history,n)end
        for n=65538,65665 do a.history[n]={seq=n,amount=20};store.touch(a.history,n)end
        store.write('run',s)
        local goodroot=globals.run
        local r=fresh_store().read('run').accounts.A.history
        for n=2,129 do assert(r[n]==nil)end
        for n=130,65537 do assert(r[n].amount==10)end
        for n=65538,65665 do assert(r[n].amount==20)end
        local pages=0
        for key in pairs(globals)do if key:find('run:account:A:history:',1,true)==1 then pages=pages+1 end end
        assert(pages==513)
        -- An older root cannot silently interpret the reused slot as its page.
        globals.run=oldroot;assert(not pcall(function()fresh_store().read('run')end))
        globals.run=goodroot;assert(fresh_store().read('run').accounts.A.history[65665].amount==20)
        ''')

    def test_same_page_revision_mismatch_fails_closed(self):
        lua = self.runtime()
        lua.execute('''
        local o={version=2,retired={['12']=true}}
        store.write('owner',o);local oldroot=globals.owner
        -- Find another identity in the same fixed bucket.
        local colliding
        local function bucket(key)local h=0;for i=1,#key do h=(h*33+key:byte(i))%256 end;return h end
        for n=13,10000 do if bucket(tostring(n))==bucket('12')then colliding=tostring(n);break end end
        assert(colliding)
        o.retired[colliding]=true;store.touch(o.retired,colliding);store.write('owner',o)
        globals.owner=oldroot
        assert(not pcall(function()fresh_store().read('owner')end))
        ''')


if __name__ == '__main__':
    unittest.main()
