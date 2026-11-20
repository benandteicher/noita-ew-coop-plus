"""Maintainer-only exact local assembly; no download/update/publish/game launch."""
from pathlib import Path
import argparse,base64,hashlib,json,re,subprocess,sys,zipfile

# Guards are intentional even when a maintainer has PYTHONOPTIMIZE set globally.
# Refuse before parsing paths, reading credentials, or creating any output.
if sys.flags.optimize:
 raise RuntimeError('Integrity checks require normal Python; unset PYTHONOPTIMIZE and do not pass -O')

RUNTIME='coopplus-20261112-b19-presence1'
BASE_SHA='64fa35097777fad9d79ffe807d9284abe7ab7c9d6453a45bf6faf2f4657ea3c1'
MANIFEST_SHA='1b4b4957f728d79106e82b9ba231fc002bc122c62402c40f7cd32565e2850e29'
DLL_SHA='5c3bec734ac40befa9a4a25976e1cd9134490a090865c4a5e7146016bf42cd66'
NAME='Noita-CoopPlus-1.9.18.12-build19-TEST-DISCORD-r1-SEALED.zip'
def digest(b):return hashlib.sha256(b).hexdigest()
def sha(p):
 with p.open('rb')as f:return hashlib.file_digest(f,'sha256').hexdigest()
def data(v):return(json.dumps(v,indent=2,ensure_ascii=False)+'\n').encode()
def zip_write(path,entries):
 with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9)as z:
  for name,content in sorted(entries.items()):
   assert not name.startswith(('/','\\'))and'..'not in name.split('/')
   info=zipfile.ZipInfo(name,(2026,11,12,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;z.writestr(info,content)
 with zipfile.ZipFile(path)as z:assert z.testzip()is None
def make_public_root(source,tree):
 # Publish only the reviewed tree. Inherited local audit/review history is never
 # pushed. commit-tree writes an object, with no parent, HEAD/index/ref changes.
 cmd=['git','--no-optional-locks','-C',str(source)]
 revision=subprocess.check_output(cmd+['-c','commit.gpgSign=false','-c','user.name=CoopPlus Reviewed TEST Source','-c','user.email=reviewed-source@example.invalid','commit-tree',tree,'-m','Reviewed standalone Co-op+ TEST19 source; tree-only public import']).decode().strip()
 assert subprocess.check_output(cmd+['rev-list','--parents','-1',revision]).decode().strip()==revision,'Public source must have no inherited parents'
 assert subprocess.check_output(cmd+['rev-parse',revision+'^{tree}']).decode().strip()==tree,'Public source tree must match frozen owner source'
 return revision

def main():
 p=argparse.ArgumentParser(allow_abbrev=False);p.add_argument('--source',required=True,type=Path);p.add_argument('--proxy',required=True,type=Path);p.add_argument('--launcher',required=True,type=Path);p.add_argument('--output',required=True,type=Path);p.add_argument('--baseline',type=Path);a=p.parse_args()
 for path in (a.source.absolute(),a.output.absolute(),a.proxy.absolute(),a.launcher.absolute()):
  for part in(path,*path.parents):
   if part.exists()and(getattr(part.lstat(),'st_file_attributes',0)&0x400 or part.is_symlink()):raise RuntimeError('Linked/reparse package paths refused')
 source=a.source.resolve();out=a.output.resolve();assert out.is_dir()and not(out/NAME).exists()
 def git(*args):return subprocess.check_output(['git','--no-optional-locks',*args],cwd=source).decode().strip()
 origin=Path(git('remote','get-url','origin')).resolve(strict=True)
 assert source==out/'owner-source'and out.is_relative_to(origin.parent)and not out.is_relative_to(origin),'Output must be the new owner-source parent inside the isolated reviewed workspace'
 assert a.proxy.resolve().is_relative_to(out/'build-target')and a.launcher.resolve()==out/'Co-op+ Playtest.exe','Only the explicit scoped build outputs may be packaged'
 assert not git('status','--porcelain'),'Owner source must be committed and clean'
 revision=git('rev-parse','HEAD');cfg=json.loads((source/'noita_proxy/discord-presence.json').read_text(encoding="utf8"));appid=cfg['application_id'];assert isinstance(appid,str)and appid.isascii()and appid.isdigit()and 17<=len(appid)<=20 and 0<int(appid)<2**64 and cfg['artwork_image']=='coopplus_logo','Registered public owner application ID required; no sample identity is substituted'
 inputs=json.loads((out/'OWNER-BUILD-INPUTS.json').read_text(encoding="utf8"));assert inputs['ownerSourceRevision']==revision and inputs['applicationId']==appid and inputs['runtimeId']==RUNTIME
 assert sha(a.proxy)==inputs['proxySha256']and sha(a.launcher)==inputs['launcherSha256'],'Compiled owner-build outputs differ from the explicit build inputs'
 compiled=Path(inputs['compiledProxyPath']);compile_root=Path(inputs['cargoTargetDirectory'])
 assert compile_root.is_absolute()and compile_root.parent==Path('C:/')and re.fullmatch(r'cp-discord-owner-b19-[a-z0-9-]{1,20}',compile_root.name),'Only the explicit fresh short compiler target is allowed'
 assert compiled==compile_root/'release/noita_proxy.exe'and Path(inputs['stagedProxyPath']).resolve()==a.proxy.resolve(),'Compiler/staged path binding differs'
 for path in(compiled,*compiled.parents):
  assert path.exists()and not path.is_symlink()and not(getattr(path.lstat(),'st_file_attributes',0)&0x400),'Linked/reparse compiler paths refused'
 assert sha(compiled)==inputs['compiledProxySha256']==inputs['proxySha256'],'Staged proxy must match the explicit compiled owner binary'

 source_name='CoopPlus-TEST19-source-'+revision[:7]+'.zip'
 assert all(not(out/name).exists()for name in(NAME,source_name,'BUILD-PROVENANCE.json','SHA256SUMS.txt','FROZEN-TEST19.json')),'Preserve any existing package output'
 if a.baseline:baseline=a.baseline
 else:
  # The initial clone's local origin identifies the review workspace; read only.
  origin=Path(git('remote','get-url','origin')).resolve();candidates=[parent/'dist/Noita-CoopPlus-1.9.18.4-build11-TEST-SETUP-r3-SEALED.zip'for parent in(origin,*origin.parents)];baseline=next((q for q in candidates if q.is_file()),None)
 assert baseline and sha(baseline)==BASE_SHA,'Pass the exact preserved published TEST11 ZIP with --baseline if workspace discovery is unavailable'
 with zipfile.ZipFile(baseline)as z:assert z.testzip()is None;entries={n:z.read(n)for n in z.namelist()}
 old_bytes=entries['payload/manifest.json'];assert digest(old_bytes)==MANIFEST_SHA;old=json.loads(old_bytes);assert old['runtimeId']=='coopplus-20261111-b11-session1';original_payload={n:b for n,b in entries.items()if n.startswith('payload/')}
 assert digest(entries['payload/mod/quant.ew/ewext.dll'])==DLL_SHA
 proxy=a.proxy.read_bytes();launcher=a.launcher.read_bytes();assert proxy[:2]==launcher[:2]==b'MZ'and RUNTIME.encode()in proxy,'Use the newly compiled frozen-runtime proxy'
 entries['payload/bin/noita_proxy.exe']=proxy
 for name in('quant.ew/init.lua','quant.ew/files/core/discord_status.lua','quant.ew/files/system/spectate/spectate.lua'):entries['payload/mod/'+name]=(source/name).read_bytes()
 runtime_key='payload/mod/quant.ew/COOPPLUS-RUNTIME.json';runtime=json.loads(entries[runtime_key]);runtime['runtimeId']=RUNTIME;runtime['proxySha256']=digest(proxy);runtime['extensionSha256']=DLL_SHA;runtime['discordPresence']=dict(sourceRevision=revision,applicationId=appid,artworkAsset=cfg['artwork_image'],scope='Local presentation only; gameplay/save/peer wire formats retained',extensionReusedSha256=DLL_SHA);entries[runtime_key]=data(runtime)
 inventory=json.loads((source/'PUBLICATION-INVENTORY.json').read_text(encoding="utf8"))['files']
 license_names=('DEPENDENCY-ADDITIONS.json','discord-rich-presence-1.1.0-LICENSE.txt','uuid-0.8.2-LICENSE-APACHE.txt','uuid-0.8.2-LICENSE-MIT.txt')
 assert set(p.name for p in(source/'presence/dependency-licenses').iterdir())==set(license_names),'Unexpected license directory content'
 tracked=set(git('ls-files','-z').strip('\0').split('\0'))
 for name in license_names:
  logical='presence/dependency-licenses/'+name;path=source/logical;assert logical in tracked and sha(path)==inventory[logical]['sha256'],'Dependency notice differs from frozen tracked input';entries['payload/bin/licenses/discord-presence/'+name]=path.read_bytes()
 files=[dict(path=n.removeprefix('payload/'),size=len(b),sha256=digest(b))for n,b in sorted(entries.items())if n.startswith('payload/')and n!='payload/manifest.json']
 manifest=dict(schema=1,runtimeId=RUNTIME,label='Co-op+ TEST 1.9.18.12 build19 / Discord r1',files=files,ownedGame=old['ownedGame'],upgradeFrom=[dict(runtimeId=old['runtimeId'],payloadHash=MANIFEST_SHA,manifestBase64=base64.b64encode(old_bytes).decode(),files=old['files'])]);entries['payload/manifest.json']=data(manifest)
 entries['Co-op+ Playtest.exe']=launcher;entries['PLAYER-INSTRUCTIONS.txt']=(source/'playtest/PLAYER-INSTRUCTIONS.txt').read_bytes()
 allowed_changes={'payload/bin/noita_proxy.exe','payload/mod/quant.ew/init.lua','payload/mod/quant.ew/files/core/discord_status.lua','payload/mod/quant.ew/files/system/spectate/spectate.lua',runtime_key,'payload/manifest.json'}
 changed=[n for n in original_payload if entries[n]!=original_payload[n]];assert set(changed)<=allowed_changes
 assert all(n in original_payload or n in allowed_changes or n.startswith('payload/bin/licenses/discord-presence/')for n in entries if n.startswith('payload/'))
 source_tree=git('rev-parse','HEAD^{tree}');public_revision=make_public_root(source,source_tree)
 provenance=dict(schema=1,sourceRevision=revision,sourceTree=source_tree,publicSourceRevision=public_revision,publicSourceTree=source_tree,publicSourceHistory='Parentless exact-tree import; no inherited local history',runtimeId=RUNTIME,baselineTestSha256=BASE_SHA,ownerApplicationId=appid,artworkAsset=cfg['artwork_image'],payloadManifestSha256=digest(entries['payload/manifest.json']),proxySha256=digest(proxy),launcherSha256=digest(launcher),extensionReusedSha256=DLL_SHA,ownedGameFingerprintsByteEquivalent=True,gameplayAlgorithmsOrSavePeerWireFormatsChanged=False,changedManagedPaths=changed,discordVisibleCardAcceptance='Not established by assembly; requires owner desktop',twoPcGameplayAcceptance='pending',normalReleaseOrRecoveryAssetsIncluded=False,licensedGameOrPrivateFilesIncluded=False)
 entries['BUILD-PROVENANCE.json']=data(provenance)
 assert not any(n.startswith('private/')or'/save00/'in n or n.endswith(('noita.exe','noita_dev.exe','.wak','.jsonl'))for n in entries)
 zip_write(out/NAME,entries)
 names=git('ls-files','-z').strip('\0').split('\0');zip_write(out/source_name,{n:(source/n).read_bytes()for n in names})
 (out/'BUILD-PROVENANCE.json').write_bytes(data(provenance));assets=[out/NAME,out/source_name,out/'BUILD-PROVENANCE.json'];(out/'SHA256SUMS.txt').write_text(''.join(sha(f)+'  '+f.name+'\n'for f in assets),encoding='utf8')
 receipt=dict(schema=1,tag='coopplus-test-v1.9.18.12-b19-discord-r1',sourceRevision=revision,sourceTree=source_tree,publicSourceRevision=public_revision,publicSourceTree=source_tree,runtimeId=RUNTIME,files={f.name:sha(f)for f in assets+[out/'SHA256SUMS.txt']},publicMutation=False,readyForPublication=False,remaining='Actual owner Discord desktop card/button/settings/exit acceptance and explicit TEST-only publication approval')
 (out/'FROZEN-TEST19.json').write_bytes(data(receipt));print(json.dumps(receipt,indent=2))
if __name__=='__main__':main()
