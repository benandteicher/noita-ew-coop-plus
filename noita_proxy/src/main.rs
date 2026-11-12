#![windows_subsystem = "windows"]

use eframe::{
    NativeOptions,
    egui::{IconData, ViewportBuilder},
};
use noita_proxy::{App, Args, connect_cli, host_cli, paths};
use std::{
    backtrace, fs,
    fs::File,
    io::{self, BufWriter},
    net::{Ipv4Addr, SocketAddr, SocketAddrV4},
    panic,
};
use tracing::{error, info, level_filters::LevelFilter};
use tracing_subscriber::EnvFilter;

#[allow(clippy::needless_return)]
#[tokio::main(worker_threads = 2)]
async fn main() {
    #[cfg(target_os = "windows")]
    {
        use winapi::um::wincon::{ATTACH_PARENT_PROCESS, AttachConsole};
        unsafe {
            AttachConsole(ATTACH_PARENT_PROCESS);
        }
    }
    let mut args: Args = argh::from_env();
    if let Err(reason) = noita_proxy::frozen_runtime::validate_entry_context(&args) {
        noita_proxy::frozen_runtime::report_entry_error(&reason);
        std::process::exit(1);
    }
    noita_proxy::frozen_runtime::configure(&mut args);
    if let Err(reason) = noita_proxy::startup_diagnostics::init() {
        noita_proxy::frozen_runtime::report_entry_error(&format!("Cannot retain startup diagnostics: {reason}"));
        std::process::exit(1);
    }
    let log = if let Ok(path) = std::env::current_exe() {
        path.parent().unwrap().join(paths::DEFAULT_PROXY_LOG_NAME)
    } else {
        paths::DEFAULT_PROXY_LOG_NAME.into()
    };
    if log.exists() {
        noita_proxy::startup_diagnostics::archive_previous_log(&log);
        let _ = fs::copy(
            log.clone(),
            log.clone()
                .parent()
                .unwrap()
                .join(paths::DEFAULT_PROXY_LOG_OLD_NAME),
        );
    }
    let file = File::create(log);
    println!("Creating a log file");
    let writer = match file {
        Ok(file) => Box::new(BufWriter::new(file)) as Box<dyn io::Write + Send>,
        Err(_) => Box::new(io::stdout()) as Box<dyn io::Write + Send>,
    };

    let (non_blocking_writer, _guard) = tracing_appender::non_blocking(writer);

    let my_subscriber = tracing_subscriber::FmtSubscriber::builder()
        .with_env_filter(
            EnvFilter::builder()
                .with_default_directive(LevelFilter::INFO.into())
                .from_env_lossy(),
        )
        .with_writer(non_blocking_writer)
        .with_ansi(false)
        .finish();

    tracing::subscriber::set_global_default(my_subscriber).expect("setting tracing default failed");

    panic::set_hook(Box::new(|info| {
        let mut payload = "no panic payload available".to_string();

        if let Some(p) = info.payload().downcast_ref::<&str>() {
            payload = p.to_string();
        }

        if let Some(p) = info.payload().downcast_ref::<String>() {
            payload = p.to_string();
        }

        match info.location() {
            Some(loc) => {
                let file = loc.file();
                let line = loc.line();
                let col = loc.column();
                error!("Panic occurred at {file}:{line}:{col} : {payload}");
            }
            None => {
                error!("Panic occurred at unknown location: {payload}");
            }
        }

        let trace = backtrace::Backtrace::force_capture();
        error!("Backtrace: {}", trace);
        noita_proxy::startup_diagnostics::failure("panic", &format!("{info}\n{trace}"));
    }));

    info!("Launch command: {:?}", args.launch_cmd);

    let outcome = panic::catch_unwind(panic::AssertUnwindSafe(|| {
    if let Some(host) = args.clone().host {
        let bind_addr = if host.eq_ignore_ascii_case("steam") {
            None
        } else {
            // allows binding to both IPv6 and IPv4
            host.parse::<SocketAddr>()
                .ok()
                // compatibility with providing only the port (which then proceeds to bind to IPv4 only)
                .or_else(|| {
                    Some(SocketAddr::V4(SocketAddrV4::new(
                        Ipv4Addr::UNSPECIFIED,
                        host.parse().ok()?,
                    )))
                })
                .map(Some)
                .expect("host argument is neither SocketAddr nor port")
        };
        host_cli(bind_addr, args);
        Ok(())
    } else if let Some(lobby) = args.clone().lobby {
        connect_cli(lobby, args);
        Ok(())
    } else {
        let icon = image::load_from_memory(include_bytes!("../assets/icon.png"))
            .unwrap()
            .to_rgba8();
        let icon = IconData {
            width: icon.width(),
            height: icon.height(),
            rgba: icon.into_vec(),
        };
        eframe::run_native(
            "Noita Proxy", // Don't change that, it defines where settings are stored.
            NativeOptions {
                viewport: ViewportBuilder::default()
                    .with_min_inner_size([800.0, 600.0])
                    .with_inner_size([1200.0, 800.0])
                    .with_icon(icon)
                    .with_title(noita_proxy::frozen_runtime::label()),
                ..Default::default()
            },
            Box::new(|cc| Ok(Box::new(App::new(cc, args)))),
        ).map_err(|error| error.to_string())
    }
    }));
    match outcome {
        Ok(Ok(())) => noita_proxy::startup_diagnostics::event("process-return", "Main event loop returned. This alone does not prove a user-requested close."),
        Ok(Err(error)) => {
            noita_proxy::startup_diagnostics::failure("native-window-error", &error);
            noita_proxy::startup_diagnostics::report_normal_failure(&error);
            std::process::exit(1);
        }
        Err(_) => {
            noita_proxy::startup_diagnostics::report_normal_failure("The proxy panicked. The first failure and backtrace were retained.");
            std::process::exit(101);
        }
    }
}
