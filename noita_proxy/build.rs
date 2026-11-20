fn main() {
    #[cfg(target_os = "linux")]
    println!("cargo:rustc-link-arg=-Wl,-rpath,$ORIGIN");

    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-env-changed=COOPPLUS_RUNTIME_ID");
    println!("cargo:rustc-check-cfg=cfg(coopplus_staging)");
    let staging = std::env::var("COOPPLUS_RUNTIME_ID")
        .is_ok_and(|id| id.starts_with("coopplus-staging-b"));
    if staging {
        println!("cargo:rustc-cfg=coopplus_staging");
    }
    if std::env::var("CARGO_CFG_TARGET_OS").unwrap() == "windows" {
        let icon = if staging { "assets/staging/icon.ico" } else { "assets/icon.ico" };
        println!("cargo:rerun-if-changed={icon}");
        let mut res = winresource::WindowsResource::new();
        res.set_icon(icon);

        res.compile().unwrap();
    }
}
