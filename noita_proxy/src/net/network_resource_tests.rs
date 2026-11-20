//! Opt-in actual NetManager directed-send measurement; no game/profile use.
use super::*;
use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;
use std::hint::black_box;

struct SendAuditAllocator;
thread_local! {
    static TRACK: Cell<bool> = const { Cell::new(false) };
    static REQUESTS: Cell<(u64,u64)> = const { Cell::new((0,0)) };
}
fn account(size: usize) {
    let _ = TRACK.try_with(|track| if track.get() {
        let _ = REQUESTS.try_with(|count| {
            let (calls,bytes)=count.get();count.set((calls+1,bytes+size as u64));
        });
    });
}
unsafe impl GlobalAlloc for SendAuditAllocator {
    unsafe fn alloc(&self,layout:Layout)->*mut u8 {
        let p=unsafe{System.alloc(layout)};if !p.is_null(){account(layout.size())}p
    }
    unsafe fn alloc_zeroed(&self,layout:Layout)->*mut u8 {
        let p=unsafe{System.alloc_zeroed(layout)};if !p.is_null(){account(layout.size())}p
    }
    unsafe fn realloc(&self,p:*mut u8,layout:Layout,size:usize)->*mut u8 {
        let result=unsafe{System.realloc(p,layout,size)};if !result.is_null(){account(size)}result
    }
    unsafe fn dealloc(&self,p:*mut u8,layout:Layout){unsafe{System.dealloc(p,layout)}}
}
#[global_allocator]
static SEND_AUDIT_ALLOCATOR:SendAuditAllocator=SendAuditAllocator;

fn payload(bytes:usize,sequence:u64)->Vec<u8> {
    let mut result=vec![0;bytes];let mut rng=sequence.wrapping_add(0x76b8a129041c83f5);
    for byte in &mut result {rng^=rng<<13;rng^=rng>>7;rng^=rng<<17;*byte=(rng>>11)as u8;}
    result[..8].copy_from_slice(&sequence.to_le_bytes());result
}
fn manager(peer:tangled::Peer,directory:PathBuf)->Arc<NetManager> {
    assert!(directory.is_absolute()&&directory.components().any(|x|x.as_os_str()=="network-resource-20261110"));
    NetManager::new(omni::PeerVariant::Tangled(peer),NetManagerInit {
        my_nickname:"audit-only".into(),save_state:SaveState::new(directory.join("save")),
        cosmetics:(false,false,false),paths:NetManagerPaths {
            noita_quantew_install:directory.join("unused-mod"),
            noita_quantew_player_spritesheet:directory.join("unused-player.png"),noita_save:None,
        },player_png_desc:PlayerPngDesc::default(),noita_port:0,
    },AudioSettings{disabled:true,..Default::default()})
}
fn percentile(values:&mut[f64],p:usize)->f64 {
    values.sort_by(f64::total_cmp);values[(values.len()-1)*p/100]
}

#[test]
fn directed_send_allocation_counter_positive_control() {
    let data=payload(8192,4);REQUESTS.with(|x|x.set((0,0)));TRACK.with(|x|x.set(true));
    let copied=data.clone();black_box(copied.as_ptr());TRACK.with(|x|x.set(false));
    assert_eq!(REQUESTS.with(Cell::get),(1,8192));assert_eq!(data,copied);assert_ne!(data.as_ptr(),copied.as_ptr());
}

#[test]
#[ignore="coordinator-only serial release actual NetManager directed sends"]
fn measure_directed_send_production_path() {
    let output=PathBuf::from(std::env::var_os("EW_CORE_BENCH_OUT").expect("set isolated output path"));
    std::fs::create_dir_all(&output).unwrap();
    let fixtures=PathBuf::from(r"C:\Projects\Noita-EW-Coop-Plus\target\network-resource-20261110\directed-send-fixtures")
        .join(format!("{}-{}",std::process::id(),std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
    let runtime=tokio::runtime::Builder::new_multi_thread().worker_threads(2).enable_all().build().unwrap();
    runtime.block_on(async {
        // Public Peer has no bound-address accessor; choose and release one
        // ephemeral loopback port before the real production host bind.
        let port=std::net::UdpSocket::bind("127.0.0.1:0").unwrap();let address=port.local_addr().unwrap();drop(port);
        let peer=tangled::Peer::host(address,None).unwrap();let client=tangled::Peer::connect(address,None).unwrap();
        let deadline=Instant::now()+Duration::from_secs(8);
        while client.my_id().is_none()||peer.iter_peer_ids().count()!=2 {
            assert!(Instant::now()<deadline);tokio::time::sleep(Duration::from_millis(2)).await;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
        peer.recv().for_each(drop);client.recv().for_each(drop);
        let recipient:OmniPeerId=client.my_id().unwrap().into();let sender=manager(peer,fixtures);
        let mut summary=String::from("path,payload_bytes,messages,allocation_requests,allocation_requested_bytes,send_p50_us,send_p95_us,send_max_us,delivered,completion_ms\n");
        for (bytes,count)in[(512usize,4096usize),(4096,2048),(81932,256)] {
            for self_path in[false,true] {
                let name=if self_path{"self_control"}else{"directed_remote"};
                let destination=if self_path{sender.peer.my_id()}else{recipient};
                let mut times=Vec::with_capacity(count);let mut raw=String::from("sequence,send_us,allocation_requests,allocation_requested_bytes\n");
                let mut calls=0;let mut requested=0;let start=Instant::now();
                for sequence in 0..count {
                    let message=NetMsg::ModRaw{data:payload(bytes,sequence as u64)};
                    REQUESTS.with(|x|x.set((0,0)));TRACK.with(|x|x.set(true));let before=Instant::now();
                    sender.send(destination,black_box(&message),Reliability::Reliable);
                    let elapsed=before.elapsed().as_secs_f64()*1e6;TRACK.with(|x|x.set(false));
                    let (alloc,amount)=REQUESTS.with(Cell::get);calls+=alloc;requested+=amount;times.push(elapsed);
                    raw.push_str(&format!("{sequence},{elapsed:.4},{alloc},{amount}\n"));
                }
                let mut delivered=0;let deadline=Instant::now()+Duration::from_secs(15);
                while delivered<count {
                    if self_path {
                        for message in sender.loopback_channel.1.try_iter() {
                            let NetMsg::ModRaw{data}=message else{panic!("wrong message type")};
                            assert_eq!(data,payload(bytes,delivered as u64));delivered+=1;
                        }
                    }else{
                        for event in client.recv() {
                            let tangled::NetworkEvent::Message(message)=event else{panic!("unexpected late control event")};
                            assert_eq!(OmniPeerId::from(message.src),sender.peer.my_id());
                            let encoded=decompress_bounded(&message.data).unwrap();
                            let NetMsg::ModRaw{data}=bitcode::decode(&encoded).unwrap()else{panic!("wrong message type")};
                            assert_eq!(data,payload(bytes,delivered as u64));delivered+=1;
                        }
                    }
                    assert!(Instant::now()<deadline,"required FIFO did not drain");
                    if delivered<count{tokio::time::sleep(Duration::from_millis(1)).await;}
                }
                let maximum=times.iter().copied().fold(0.,f64::max);let p50=percentile(&mut times,50);let p95=percentile(&mut times,95);
                let elapsed=start.elapsed().as_secs_f64()*1e3;
                let line=format!("{name},{bytes},{count},{calls},{requested},{p50:.4},{p95:.4},{maximum:.4},{delivered},{elapsed:.4}\n");
                summary.push_str(&line);eprint!("OUTER_SEND {line}");
                std::fs::write(output.join(format!("{name}-{bytes}-raw.csv")),raw).unwrap();
                assert!(client.recv().all(|e|!matches!(e,tangled::NetworkEvent::Message(_))),"duplicate wire suffix");
                assert!(sender.loopback_channel.1.is_empty(),"duplicate self suffix");
            }
        }
        std::fs::write(output.join("outer-send-summary.csv"),summary).unwrap();
        drop(sender);drop(client);tokio::time::sleep(Duration::from_millis(1150)).await;
    });
}
