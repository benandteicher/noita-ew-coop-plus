use std::{
    fmt::Display,
    mem,
    net::SocketAddr,
    path::PathBuf,
    process::exit,
    sync::{Arc, atomic::Ordering},
    time::Duration,
};

use arboard::Clipboard;
use image::{DynamicImage::ImageRgba8, RgbaImage};
use mod_manager::Modmanager;
use self_update::SelfUpdateManager;
use serde::{Deserialize, Serialize};
use steamworks::{LobbyId, SteamAPIInitError};
use tangled::{Peer, Reliability};
use tokio::time;
use tracing::{error, info};
use unic_langid::LanguageIdentifier;

use eframe::egui::{
    self, Align2, Button, Color32, Context, FontDefinitions, FontFamily, ImageButton,
    InnerResponse, Key, Layout, Margin, OpenUrl, Rect, RichText, ScrollArea, Slider,
    TextureOptions, ThemePreference, Ui, UiBuilder, Vec2, Visuals, Window,
};

use crate::{
    AudioSettings, DefaultSettings, GameSettings, ImageMap, NetManStopOnDrop, PlayerAppearance,
    bookkeeping::{
        mod_manager,
        noita_launcher::{LaunchTokenResult, NoitaLauncher},
        releases::Version,
        save_paths::SavePaths,
        save_state::SaveState,
        self_restart::SelfRestarter,
        self_update,
        settings::Settings,
        run_settings::{SavedRunSettings,SessionRules},
    },
    cli::Args,
    lang::{set_current_locale, tr},
    lobby_code::{LobbyCode, LobbyError, LobbyKind},
    net::{
        NetManager, NetManagerInit, NetManagerPaths, RunInfo,
        messages::NetMsg,
        omni::{OmniPeerId, PeerVariant},
        steam_networking,
    },
    parse_connect_addr,
    paths::{self, Paths},
    player_cosmetics::{PlayerPngDesc, display_player_skin},
    steam_helper,
    util::steam_helper::LobbyExtraData,
};
use crate::{DEFAULT_PORT, lang::LANGS};

#[path = "log_reader.rs"]
mod log_reader;
use log_reader::LogReader;

fn show_log_context(ui: &mut Ui, id: egui::Id, text: &str, max_height: f32) {
    let mut target = None;
    ui.horizontal(|ui| {
        if ui.small_button("Start").clicked() { target = Some(egui::Align::TOP); }
        if ui.small_button("End").clicked() { target = Some(egui::Align::BOTTOM); }
    });
    ScrollArea::vertical()
        .id_salt(id)
        .max_height(max_height)
        .min_scrolled_height(0.0)
        .auto_shrink([false, true])
        .show(ui, |ui| {
            let start = ui.cursor().min;
            ui.add(egui::Label::new(RichText::new(text).monospace()).wrap());
            if let Some(target) = target {
                if target == egui::Align::TOP {
                    ui.scroll_to_rect(Rect::from_min_size(start, Vec2::ZERO), Some(target));
                } else {
                    ui.scroll_to_cursor(Some(target));
                }
            }
        });
}

fn show_log(ui: &mut Ui, log: &LogReader, clipboard: &mut Option<Clipboard>) {
    ui.label("Partial capture: recent 256 KiB / 4096 lines; lines limited to 4096 bytes. First general and first specific communication contexts: up to 16 KiB / 9 lines each. Earlier runs are not retained. This view does not modify the log files.");
    ui.label(log.source_path().display().to_string());
    ui.label(format!(
        "Generation {} · source {} bytes · capture {} bytes · about {} older bytes omitted",
        log.generation,
        log.source_len(),
        log.retained_bytes(),
        log.omitted_bytes
    ));
    if log.catching_up() {
        ui.label("Reading recent output and scanning for the first failure in bounded steps…");
    }
    if log.invalid_utf8 {
        ui.label("Invalid UTF-8 is displayed with replacement characters; other log output remains available.");
    }
    if let Some(error) = &log.error {
        ui.colored_label(Color32::LIGHT_RED, error);
    }
    // Keep two expanded contexts from consuming the recent log's viewport,
    // including at the 800x600 minimum with the 230px players sidebar.
    let context_height = (ui.available_height() * 0.16).clamp(32.0, 80.0);
    if let Some(failure) = &log.first_failure {
        ui.collapsing(format!("First failure-like context found by sequential scan (generation {}; heuristic match)", failure.generation), |ui| {
            show_log_context(ui, egui::Id::new(("general_log_context", failure.generation, failure.byte_offset)), &failure.text, context_height);
        });
    }
    if let Some(failure) = &log.first_communication_failure {
        ui.collapsing(format!("First specific communication failure observed (generation {}, byte {}; heuristic match)", failure.generation, failure.byte_offset), |ui| {
            if !failure.earlier_scanned {
                ui.label(if failure.generation == log.generation {
                    "Provisional: earlier bytes have not yet been verified. The sequential scan may find an earlier communication failure."
                } else {
                    "Provisional: this earlier log generation ended before its earlier bytes were verified."
                });
            }
            show_log_context(ui, egui::Id::new(("communication_log_context", failure.generation, failure.byte_offset)), &failure.text, context_height);
        });
    }
    if ui.add_enabled(clipboard.is_some(), Button::new("Copy partial capture")).clicked()
        && let Some(clipboard) = clipboard.as_mut()
    {
        let _ = clipboard.set_text(log.capture_text());
    }
    // Virtualized, unwrapped lines: unchanged frames lay out visible rows only.
    let height = ui.text_style_height(&egui::TextStyle::Monospace);
    ScrollArea::both()
        .auto_shrink([false; 2])
        .stick_to_bottom(true)
        .show_rows(ui, height, log.row_count(), |ui, rows| {
            for row in rows {
                ui.add(egui::Label::new(RichText::new(log.row(row)).monospace()).extend());
            }
        });
}

enum AppState {
    Connect,
    ModManager,
    TangledConnecting {
        peer: Peer,
    },
    ConnectedLobby {
        netman: NetManStopOnDrop,
        noita_launcher: NoitaLauncher,
    },
    Error {
        message: String,
    },
    SelfUpdate,
    LangPick,
    AskSavestateReset,
    GogModeIssue(LobbyCode),
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum ConnectedMenu {
    Normal,
    Settings,
    Mods,
    BanList,
    ConnectionInfo,
    VoIP,
    Map,
    NoitaLog,
    ProxyLog,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(default)]
pub struct AppSavedState {
    pub addr: String,
    pub nickname: Option<String>,
    pub times_started: u32,
    pub lang_id: Option<LanguageIdentifier>,
    #[serde(default)]
    pub game_settings: GameSettings,
    pub start_game_automatically: bool,
    #[serde(default)]
    pub show_extra_debug_stuff: bool,
    #[serde(default)]
    pub record_all: bool,
    pub spacewars: bool,
    pub random_ports: bool,
    pub public_lobby: bool,
    pub allow_friends: bool,
}

impl Default for AppSavedState {
    fn default() -> Self {
        Self {
            addr: "127.0.0.1:5123".to_string(),
            nickname: None,
            times_started: 0,
            lang_id: None,
            game_settings: GameSettings::default(),
            start_game_automatically: false,
            show_extra_debug_stuff: false,
            record_all: false,
            spacewars: false,
            random_ports: false,
            public_lobby: false,
            allow_friends: true,
        }
    }
}

pub struct App {
    state: AppState,
    audio: AudioSettings,
    modmanager: Modmanager,
    steam_state: Result<steam_helper::SteamState, SteamAPIInitError>,
    app_saved_state: AppSavedState,
    run_save_state: SaveState,
    self_update: SelfUpdateManager,
    lobby_id_field: String,
    args: Args,
    /// `true` if we haven't started noita automatically yet.
    can_start_automatically: bool,
    player_image: RgbaImage,
    end_run_button: EndRunButton,
    appearance: PlayerAppearance,
    connected_menu: ConnectedMenu,
    show_host_settings: bool,
    show_audio_settings: bool,
    running_on_steamdeck: bool,
    copied_lobby: bool,
    my_lobby_kind: LobbyKind,
    show_lobby_list: bool,
    map: ImageMap,
    refresh_timer: time::Instant,
    noitalog: LogReader,
    proxylog: LogReader,
    clipboard: Option<Clipboard>,
    paths: Paths,
    new_run_intent: bool,
    legacy_startup_settings: GameSettings,
    settings_save_error: Option<String>,
    user_close_reported: bool,
}

impl Drop for App {
    fn drop(&mut self) {
        self.set_settings();
    }
}
impl App {
    pub fn new(cc: &eframe::CreationContext<'_>, args: Args) -> Self {
        cc.egui_ctx.set_visuals(Visuals::dark());
        cc.egui_ctx.set_theme(ThemePreference::Dark);
        let save_paths = SavePaths::new_with_maybe_override(
            args.settings_path.clone(),
            args.save_state_path.clone(),
        );
        let settings = save_paths.load_settings();
        let Settings {
            color: appearance,
            app: mut saved_state,
            audio,
            mut paths,
        } = settings;
        paths.proxy_settings = Some(save_paths.settings_path.clone());
        paths.proxy_save_state = Some(save_paths.save_state_path.clone());
        paths.steam_install = paths::get_steam_install();
        crate::frozen_runtime::route_paths(&mut paths);
        if crate::frozen_runtime::private_root().is_some() { saved_state.spacewars = false; }
        saved_state.times_started += 1;

        if paths.noita_exe.is_some() {
            paths::realize_noita_paths_from_noita_exe(&mut paths);
        }

        info!("Setting fonts...");
        Self::set_fonts(&cc.egui_ctx);
        let state = if let Some(lang_id) = &saved_state.lang_id {
            set_current_locale(lang_id.clone());
            AppState::ModManager
        } else {
            AppState::LangPick
        };

        info!("Installing image loaders...");
        egui_extras::install_image_loaders(&cc.egui_ctx);

        let my_lobby_kind = args.override_lobby_kind.unwrap_or({
            if saved_state.spacewars {
                LobbyKind::Gog
            } else {
                LobbyKind::Steam
            }
        });

        info!("Initializing steam state...");
        let steam_state = steam_helper::SteamState::new(my_lobby_kind == LobbyKind::Gog);

        info!("Checking if running on steam deck...");
        let running_on_steamdeck = steam_state
            .as_ref()
            .map(|steam| steam.client.utils().is_steam_running_on_steam_deck())
            .unwrap_or(false);
        let default_zoom_factor = if running_on_steamdeck { 0.3 } else { 1.0 };
        if running_on_steamdeck {
            cc.egui_ctx
                .send_viewport_cmd(egui::ViewportCommand::MinInnerSize(Vec2::new(10.0, 10.0)));
            cc.egui_ctx
                .send_viewport_cmd(egui::ViewportCommand::Fullscreen(true));
        }

        cc.egui_ctx
            .set_zoom_factor(args.ui_zoom_factor.unwrap_or(default_zoom_factor));
        info!("Creating the app...");
        let run_save_state = SaveState::new(&save_paths.save_state_path);
        let player_image = if let Some(path) = &paths.noita_quantew_player_spritesheet
            && path.exists()
        {
            image::open(path)
                .unwrap_or(ImageRgba8(RgbaImage::new(20, 20)))
                .crop(1, 1, 7, 16)
                .into_rgba8()
        } else {
            RgbaImage::new(7, 17)
        };

        let legacy_startup_settings=saved_state.game_settings.clone();
        let mut me = Self {
            state,
            audio,
            modmanager: Modmanager::default(),
            steam_state,
            app_saved_state: saved_state,
            self_update: SelfUpdateManager::new(),
            lobby_id_field: "".to_string(),
            args,
            can_start_automatically: false,
            run_save_state,
            player_image,
            end_run_button: EndRunButton::default(),
            appearance,
            connected_menu: ConnectedMenu::Normal,
            show_host_settings: false,
            show_audio_settings: false,
            running_on_steamdeck,
            copied_lobby: true,
            my_lobby_kind,
            show_lobby_list: false,
            map: Default::default(),
            refresh_timer: time::Instant::now(),
            noitalog: LogReader::default(),
            proxylog: LogReader::default(),
            clipboard: Clipboard::new().ok(),
            paths,
            new_run_intent:false,legacy_startup_settings,settings_save_error:None,user_close_reported:false,
        };

        if let Some(connect_to) = me.args.auto_connect_to {
            me.start_steam_connect(connect_to.code);
        }

        me
    }

    fn set_settings(&mut self)->bool {
        let mut audio = self.audio.clone();
        audio.input_devices.clear();
        audio.output_devices.clear();

        let result = Settings {
            color: self.appearance.clone(),
            app: self.app_saved_state.clone(),
            paths: self.paths.clone(),
            audio,
        }
        .save(self.paths.proxy_settings());
        match result {
            Ok(()) => {self.settings_save_error=None;true},
            Err(e) => {error!("Failed to save settings: {e}");crate::startup_diagnostics::failure("settings-save-failed",&e.to_string());self.settings_save_error=Some(format!("Settings were not saved: {e}. Retry before closing or creating a lobby."));false},
        }
    }

    fn get_netman_init(&self) -> NetManagerInit {
        let my_nickname = self.nickname();
        let mut cosmetics = self.appearance.cosmetics;
        if let Some(path) = &self.paths.noita_save {
            let flags = path.join("save00/persistent/flags");
            let hat = flags.join("secret_hat").exists();
            let amulet = flags.join("secret_amulet").exists();
            let gem = flags.join("secret_amulet_gem").exists();
            if !hat {
                cosmetics.0 = false
            }
            if !amulet {
                cosmetics.1 = false
            }
            if !gem {
                cosmetics.2 = false
            }
        }
        let noita_port = if self.app_saved_state.random_ports {
            0
        } else {
            21251
        };

        let paths = NetManagerPaths::try_from_paths(&self.paths).expect("necessary paths are some");

        NetManagerInit {
            my_nickname,
            save_state: if self.new_run_intent {self.run_save_state.for_new_run()}else{self.run_save_state.for_session()},
            cosmetics,
            paths,
            player_png_desc: PlayerPngDesc {
                cosmetics: cosmetics.into(),
                colors: self.appearance.player_color,
                invert_border: self.appearance.invert_border,
            },
            noita_port,
        }
    }

    fn nickname(&self) -> String {
        let default = "no name found".to_string();
        let steam_nickname = if let Ok(steam) = &self.steam_state {
            let sid = steam.get_my_id();
            Some(steam.get_user_name(sid))
        } else {
            None
        };
        let mut my_nickname = self.app_saved_state.nickname.clone().or(steam_nickname);
        if let Some(n) = &my_nickname
            && n.trim().is_empty()
        {
            my_nickname = None;
        }

        my_nickname.unwrap_or(default)
    }

    fn change_state_to_netman(&mut self, netman: Arc<NetManager>, player_path: PathBuf) {
        self.copied_lobby = false;
        let handle = netman.clone().start(player_path);
        self.state = AppState::ConnectedLobby {
            netman: NetManStopOnDrop(netman, Some(handle)),
            noita_launcher: NoitaLauncher::new(
                &self.paths,
                self.args.launch_cmd.as_deref(),
                self.steam_state.as_mut().ok(),
            ),
        };
        self.can_start_automatically = true;
    }

    fn start_server(&mut self) {
        if crate::frozen_runtime::ID.is_some() { self.notify_error("This frozen Co-op+ runtime requires Steam host/join for matching-peer checks."); return; }
        let bind_addr = SocketAddr::new("::".parse().unwrap(), DEFAULT_PORT);
        let peer = Peer::host(bind_addr, None).unwrap();
        let netman = NetManager::new(
            PeerVariant::Tangled(peer),
            self.get_netman_init(),
            self.audio.clone(),
        );
        if let Err(error)=self.set_netman_settings(&netman){self.notify_error(error);return;}
        self.change_state_to_netman(
            netman,
            self.paths.noita_quantew_player_spritesheet().clone(),
        );
    }

    fn host_settings_selection(&self)->std::io::Result<(GameSettings,SessionRules)> {
        let run_info: Option<RunInfo> = if self.new_run_intent{None}else{self.run_save_state.load_checked()?};
        let mut active=self.app_saved_state.game_settings.clone();let session;
        if let Some(info)=run_info {
            if let Some(saved)=SavedRunSettings::load(self.run_save_state.directory(),info.seed)? {active=saved.settings.clone();session=SessionRules::from_saved(&saved);}
            else {active=self.legacy_startup_settings.clone();active.seed=info.seed;session=SessionRules::legacy();info!("Legacy Continue: preserving startup proxy preferences; historical run rules are unverified");}
        } else {if !active.use_constant_seed{active.seed=rand::random();}session=SessionRules::default();}
        info!("Authoritative host rules selected: mode={} seed={} legacy_unverified={}",active.game_mode.unwrap_or(DefaultSettings::default().game_mode),active.seed,session.legacy_unverified);
        crate::startup_diagnostics::event("host-settings-selected",&format!("mode={} seed={} legacy_unverified={} new_intent={}",active.game_mode.unwrap_or(DefaultSettings::default().game_mode),active.seed,session.legacy_unverified,self.new_run_intent));
        active.progress =
            mod_manager::get_progress(self.paths.noita_save.as_deref()).unwrap_or_default();
        Ok((active,session))
    }
    fn set_netman_settings(&mut self, netman: &Arc<NetManager>)->std::io::Result<()> {
        let (active,session)=self.host_settings_selection()?;
        *netman.settings.lock().unwrap()=active.clone();*netman.pending_settings.lock().unwrap()=active;
        *netman.session_rules.lock().unwrap()=session;netman.apply_host_preferences(&self.app_saved_state.game_settings);Ok(())
    }

    fn start_connect(&mut self, addr: SocketAddr) {
        if crate::frozen_runtime::ID.is_some() { self.notify_error("This frozen Co-op+ runtime requires Steam host/join for matching-peer checks."); return; }
        let peer = Peer::connect(addr, None).unwrap();
        self.state = AppState::TangledConnecting { peer };
    }

    fn start_connect_step_2(&mut self, peer: Peer) {
        let netman = NetManager::new(
            PeerVariant::Tangled(peer),
            self.get_netman_init(),
            self.audio.clone(),
        );
        self.change_state_to_netman(
            netman,
            self.paths.noita_quantew_player_spritesheet().clone(),
        );
    }

    fn start_steam_host(&mut self) {
        let (active,_)=match self.host_settings_selection(){Ok(value)=>value,Err(error)=>{self.notify_error(error);return;}};
        let lobby_type = if self.app_saved_state.public_lobby {
            steamworks::LobbyType::Public
        } else if self.app_saved_state.allow_friends {
            steamworks::LobbyType::FriendsOnly
        } else {
            steamworks::LobbyType::Private
        };
        let peer = match steam_networking::SteamPeer::new_host(
            lobby_type,
            self.steam_state.as_ref().unwrap().client.clone(),
            active
                .max_players
                .unwrap_or(DefaultSettings::default().max_players),
            LobbyExtraData {name:self.nickname(),game_mode:Some(active.game_mode.unwrap_or(DefaultSettings::default().game_mode))},
        ){Ok(peer)=>peer,Err(error)=>{self.notify_error(error);return;}};
        let netman = NetManager::new(
            PeerVariant::Steam(peer),
            self.get_netman_init(),
            self.audio.clone(),
        );
        if let Err(error)=self.set_netman_settings(&netman){self.notify_error(error);return;}
        self.change_state_to_netman(
            netman,
            self.paths.noita_quantew_player_spritesheet().clone(),
        );
    }

    fn make_lobby_extra_data(&self) -> LobbyExtraData {
        let mode=if let AppState::ConnectedLobby{netman,..}=&self.state {netman.settings.lock().unwrap().game_mode}else{self.app_saved_state.game_settings.game_mode};
        LobbyExtraData {
            name: self.nickname(),
            game_mode: Some(mode.unwrap_or(DefaultSettings::default().game_mode)),
        }
    }

    fn notify_error(&mut self, error: impl Display) {
        crate::startup_diagnostics::event("ui-error",&error.to_string());
        self.state = AppState::Error {
            message: error.to_string(),
        }
    }

    fn start_steam_connect(&mut self, id: LobbyId) {
        let peer = match steam_networking::SteamPeer::new_connect(
            id,
            self.steam_state.as_ref().unwrap().client.clone(),
        ){Ok(peer)=>peer,Err(error)=>{self.notify_error(error);return;}};

        let netman = NetManager::new(
            PeerVariant::Steam(peer),
            self.get_netman_init(),
            self.audio.clone(),
        );
        self.change_state_to_netman(
            netman,
            self.paths.noita_quantew_player_spritesheet().clone(),
        );
    }

    fn connect_screen(&mut self, ctx: &Context) {
        egui::CentralPanel::default().show(ctx, |ui| {
            if self.app_saved_state.times_started.is_multiple_of(20) {
                let image = egui::Image::new(egui::include_image!("../assets/longleg.png"))
                    .texture_options(TextureOptions::NEAREST);
                image.paint_at(ui, ctx.screen_rect());
            } else {
                draw_bg(ui);
            }

            let group_shrink = ui.spacing().item_spacing.x * 0.5;
            let rect = ui.max_rect();
            let (rect, bottom_panel) =
                rect.split_top_bottom_at_y(rect.height() - (25.0 + group_shrink * 2.0));

            let (rect, right_b_panel) =
                rect.split_left_right_at_x(rect.width() - (50.0 + group_shrink * 2.0));
            let (settings_rect, right) = rect.split_left_right_at_fraction(0.5);
            let (steam_connect_rect, other_rect) = right.split_top_bottom_at_fraction(0.33);
            let (ip_connect_rect, info_rect) = other_rect.split_top_bottom_at_fraction(0.5);

            ui.allocate_new_ui(
                UiBuilder {
                    max_rect: Some(bottom_panel.shrink(group_shrink)),
                    ..Default::default()
                },
                |ui| {
                    filled_group(ui, |ui| {
                        ui.set_min_size(ui.available_size());
                        self.self_update.display_version(ui);

                        if self.self_update.request_update {
                            self.state = AppState::SelfUpdate;
                        }
                    });
                },
            );

            ui.allocate_new_ui(
                UiBuilder {
                    max_rect: Some(info_rect.shrink(group_shrink)),
                    ..Default::default()
                },
                |ui| {
                    filled_group(ui, |ui| {
                        ui.set_min_size(ui.available_size());
                        // heading_with_underline(ui, tr("Info"));
                        // ui.label(tr("info_stress_tests"));
                    });
                },
            );

            ui.allocate_new_ui(
                UiBuilder {
                    max_rect: Some(right_b_panel.shrink(group_shrink)),
                    ..Default::default()
                },
                |ui| {
                    filled_group(ui, |ui| {
                        ui.set_min_size(ui.available_size());

                        self.panel_right_bar(ui, ctx);
                    })
                },
            );

            ui.allocate_new_ui(
                UiBuilder {
                    max_rect: Some(settings_rect.shrink(group_shrink)),
                    ..Default::default()
                },
                |ui| {
                    filled_group(ui, |ui| {
                        ui.set_min_size(ui.available_size());
                        ScrollArea::both().auto_shrink(false).show(ui, |ui| {
                            self.show_local_settings(ui);
                            if ui.button("Show host settings").clicked() {
                                self.show_host_settings = !self.show_host_settings
                            }
                            if self.show_host_settings {
                                ui.label(if self.run_save_state.has_run_info() && !self.new_run_intent{"Next-run preferences. Continue keeps the recorded/current run rules."}else{"These host settings apply to the first game; no proxy restart is needed."});
                                if self.app_saved_state.game_settings.show_editor(ui, true){self.set_settings();}
                            }
                            if let Some(error)=&self.settings_save_error{ui.colored_label(Color32::LIGHT_RED,error);}
                            if ui.button("Show audio settings").clicked() {
                                self.show_audio_settings = !self.show_audio_settings
                            }
                            if self.show_audio_settings {
                                self.audio.show_ui(ui, true);
                            }
                            if self.running_on_steamdeck && ui.button("Close Proxy").clicked() {
                                exit(0)
                            }
                        });
                    });
                },
            );
            ui.allocate_new_ui(
                UiBuilder {
                    max_rect: Some(steam_connect_rect.shrink(group_shrink)),
                    ..Default::default()
                },
                |ui| {
                    filled_group(ui, |ui| {
                        ui.set_min_size(ui.available_size());

                        self.panel_connect_by_steam(ui);
                    });
                },
            );
            ui.allocate_new_ui(
                UiBuilder {
                    max_rect: Some(ip_connect_rect.shrink(group_shrink)),
                    ..Default::default()
                },
                |ui| {
                    filled_group(ui, |ui| {
                        ui.set_min_size(ui.available_size());

                        self.panel_connect_by_ip(ui);
                    });
                },
            );
        });

        if self.show_lobby_list {
            let mut connect_to = None;
            Window::new(tr("Lobby-list"))
                .open(&mut self.show_lobby_list)
                .show(ctx, |ui| {
                    ui.set_min_height(100.0);
                    let steam_state = self.steam_state.as_mut().unwrap();
                    ui.horizontal(|ui| {
                        if ui.button(tr("Refresh")).clicked() {
                            steam_state.update_lobby_list(&mut self.refresh_timer);
                        }
                    });
                    ScrollArea::vertical().show(ui, |ui| {
                        match steam_state.list_lobbies(&mut self.refresh_timer) {
                            steam_helper::MaybeLobbyList::Pending => {
                                ui.label(tr("Lobby-list-pending"));
                            }
                            steam_helper::MaybeLobbyList::List(lobby_ids) => {
                                let mut shown_anything = false;
                                for id in lobby_ids.iter() {
                                    let info = steam_state.lobby_info(*id);
                                    if info.version != Some(crate::frozen_runtime::peer_version()) {
                                        continue;
                                    }
                                    shown_anything = true;
                                    ui.group(|ui| {
                                        ui.set_max_height(50.0);
                                        ui.horizontal(|ui| {
                                            ui.colored_label(Color32::WHITE, info.data.name);
                                            ui.with_layout(
                                                Layout::right_to_left(egui::Align::Center),
                                                |ui| {
                                                    ui.label(format!(
                                                        "{}/{}",
                                                        info.member_count, info.member_limit
                                                    ));
                                                    if let Some(game_mode) = &info.data.game_mode {
                                                        ui.label(" - ");
                                                        let color = game_mode.color();
                                                        ui.colored_label(
                                                            color,
                                                            tr(&format!("game_mode_{game_mode}")),
                                                        );
                                                    }
                                                },
                                            );
                                        });
                                        ui.horizontal(|ui| {
                                            let mut enabled = false;
                                            if let Some(version) = info.version {
                                                enabled = version == crate::frozen_runtime::peer_version();
                                                let color = if enabled {
                                                    Color32::GRAY
                                                } else {
                                                    Color32::RED
                                                };
                                                ui.colored_label(color, format!("EW {version}"));
                                            } else if info.is_noita_online {
                                                ui.colored_label(
                                                    Color32::LIGHT_BLUE,
                                                    "Noita Online",
                                                );
                                            } else {
                                                ui.label(tr("Not-Entangled-Worlds-lobby"));
                                            }
                                            ui.with_layout(
                                                Layout::right_to_left(egui::Align::Center),
                                                |ui| {
                                                    ui.add_enabled_ui(enabled, |ui| {
                                                        if ui.small_button(tr("Join")).clicked() {
                                                            connect_to = Some(*id);
                                                        }
                                                    });
                                                },
                                            );
                                        });
                                    });
                                }
                                if !shown_anything {
                                    ui.label(tr("No-public-lobbies-at-the-moment"));
                                }
                            }
                            steam_helper::MaybeLobbyList::Errored => {
                                ui.label("Failed to request lobby list");
                            }
                        }
                    });
                });
            if let Some(lobby) = connect_to {
                self.start_steam_connect(lobby);
            }
        }
    }

    fn panel_right_bar(&mut self, ui: &mut Ui, ctx: &Context) {
        let lang_label = self
            .app_saved_state
            .lang_id
            .clone()
            .unwrap_or_default()
            .language;
        if square_button_text(ui, &lang_label.to_string().to_uppercase(), 21.0)
            .on_hover_text(tr("button_set_lang"))
            .clicked()
        {
            self.state = AppState::LangPick;
        }
        if square_button_icon(
            ui,
            egui::Image::new(egui::include_image!("../assets/discord-mark-white.png")),
        )
        .on_hover_text(tr("button_open_discord"))
        .clicked()
        {
            ctx.open_url(OpenUrl::new_tab("https://discord.gg/uAK7utvVWN"));
        }
        let secret_active = ui.input(|i| i.modifiers.ctrl && i.key_down(Key::D));
        if secret_active && ui.button("reset all data").clicked() {
            self.app_saved_state = Default::default();
            self.paths = Default::default();
            self.state = AppState::LangPick;
        }
    }

    fn panel_connect_by_steam(&mut self, ui: &mut Ui) {
        heading_with_underline(ui, tr("connect_steam"));

        match &self.steam_state {
            Ok(_) => {
                if ui.button(tr("connect_steam_create")).clicked() {
                    if self.set_settings(){self.start_steam_host();}
                }
                ui.checkbox(
                    &mut self.app_saved_state.public_lobby,
                    tr("Make-lobby-public"),
                );
                ui.checkbox(&mut self.app_saved_state.allow_friends, "Allow friends");
                if ui.button(tr("connect_steam_connect")).clicked() {
                    let id = self.clipboard.as_mut().and_then(|c| c.get_text().ok());
                    match id {
                        Some(id) => {
                            self.set_settings();
                            self.connect_to_steam_lobby(id);
                        }
                        None => self.notify_error(if self.clipboard.is_none() {
                            "no clipboard"
                        } else {
                            "clipboard failed"
                        }),
                    }
                }
                if ui.button(tr("Open-lobby-list")).clicked() {
                    self.show_lobby_list = true;
                }

                if cfg!(target_os = "linux") {
                    ui.add_space(15.0);
                    ui.label(tr("connect_steam_workaround_label"));
                    ui.text_edit_singleline(&mut self.lobby_id_field);
                    if ui.button(tr("connect_steam_connect_2")).clicked() {
                        self.set_settings();
                        self.connect_to_steam_lobby(self.lobby_id_field.clone());
                    }
                }
            }
            Err(err) => {
                ui.label(format!("Could not init steam networking: {err}"));
            }
        }
    }

    fn panel_connect_by_ip(&mut self, ui: &mut Ui) {
        heading_with_underline(ui, tr("connect_ip"));

        ui.label(tr("ip_note"));
        if ui.button(tr("ip_host")).clicked() {
            self.set_settings();
            self.start_server();
        }

        ui.text_edit_singleline(&mut self.app_saved_state.addr);
        ui.add_enabled_ui(!self.app_saved_state.addr.trim().is_empty(), |ui| {
            if ui.button(tr("ip_connect")).clicked() {
                match parse_connect_addr(&self.app_saved_state.addr) {
                    Ok(addr) => {
                        self.set_settings();
                        self.start_connect(addr);
                    }
                    Err(err) => self.notify_error(err),
                }
            }
        });
    }

    fn show_local_settings(&mut self, ui: &mut Ui) {
        heading_with_underline(ui, tr("connect_settings_local"));
        {
            let mut temp = self.app_saved_state.nickname.clone().unwrap_or(
                if let Ok(steam) = &self.steam_state {
                    steam.get_user_name(steam.get_my_id())
                } else {
                    "NO NICKNAME CHOSEN".to_string()
                },
            );
            if ui
                .horizontal(|ui| {
                    ui.label("nickname");
                    ui.text_edit_singleline(&mut temp)
                })
                .inner
                .changed()
            {
                self.app_saved_state.nickname = Some(temp)
            }
        }
        ui.checkbox(
            &mut self.app_saved_state.start_game_automatically,
            tr("connect_settings_autostart"),
        );
        ui.checkbox(
            &mut self.app_saved_state.spacewars,
            tr("connect_settings_spacewars"),
        );
        ui.checkbox(
            &mut self.app_saved_state.random_ports,
            tr("connect_settings_random_ports"),
        );
        if self.player_image.width() == 1 {
            self.player_image = image::open(self.paths.noita_quantew_player_spritesheet())
                .unwrap_or(ImageRgba8(RgbaImage::new(20, 20)))
                .crop(1, 1, 7, 16)
                .into_rgba8();
        }
        ui.add_space(20.0);
        self.appearance.mina_color_picker(
            ui,
            self.paths.noita_save.clone(),
            self.player_image.clone(),
        );
    }

    fn connect_to_steam_lobby(&mut self, lobby_id_raw: String) {
        let lobby_id_raw = lobby_id_raw
            .chars()
            .filter(|c| !c.is_whitespace())
            .collect::<String>();
        let lobby = LobbyCode::parse(&lobby_id_raw);

        match lobby {
            Ok(lobby) => {
                if self.my_lobby_kind == lobby.kind {
                    self.start_steam_connect(lobby.code)
                } else {
                    self.state = AppState::GogModeIssue(lobby);
                }
            }
            Err(LobbyError::NotALobbyCode) => {
                self.notify_error(tr("connect_steam_connect_invalid_lobby_id"))
            }
            Err(LobbyError::CodeVersionMismatch) => self
                .notify_error("Lobby code was created by a newer version of proxy. Please update"),
        }
    }

    fn set_fonts(ctx: &Context) {
        let mut font_definitions = FontDefinitions::default();

        font_definitions.font_data.insert(
            "noto_sans".to_owned(),
            egui::FontData::from_static(include_bytes!("../assets/font/NotoSans-Regular.ttf"))
                .into(),
        );
        font_definitions.font_data.insert(
            "noto_sans_jp".to_owned(),
            egui::FontData::from_static(include_bytes!("../assets/font/NotoSansJP-Light.ttf"))
                .into(),
        );
        font_definitions.font_data.insert(
            "noto_sans_sc".to_owned(),
            egui::FontData::from_static(include_bytes!("../assets/font/MiSans-Light.ttf")).into(),
        );
        font_definitions.font_data.insert(
            "noto_sans_kr".to_owned(),
            egui::FontData::from_static(include_bytes!("../assets/font/NotoSansKR-Light.ttf"))
                .into(),
        );

        font_definitions
            .families
            .entry(FontFamily::Proportional)
            .or_default()
            .push("noto_sans".to_owned());
        font_definitions
            .families
            .entry(FontFamily::Proportional)
            .or_default()
            .push("noto_sans_jp".to_owned());

        font_definitions
            .families
            .entry(FontFamily::Monospace)
            .or_default()
            .push("noto_sans".to_owned());
        font_definitions
            .families
            .entry(FontFamily::Monospace)
            .or_default()
            .push("noto_sans_jp".to_owned());

        font_definitions
            .families
            .entry(FontFamily::Proportional)
            .or_default()
            .push("noto_sans_sc".to_owned());
        font_definitions
            .families
            .entry(FontFamily::Monospace)
            .or_default()
            .push("noto_sans_sc".to_owned());

        font_definitions
            .families
            .entry(FontFamily::Proportional)
            .or_default()
            .push("noto_sans_kr".to_owned());
        font_definitions
            .families
            .entry(FontFamily::Monospace)
            .or_default()
            .push("noto_sans_kr".to_owned());

        ctx.set_fonts(font_definitions);
    }

    fn switch_to_connect(&mut self) {
        self.state = if self.run_save_state.has_run_info() {
            AppState::AskSavestateReset
        } else {
            AppState::Connect
        };
    }

    fn show_lobby(&mut self, ctx: &Context) {
        let AppState::ConnectedLobby {
            netman,
            noita_launcher,
        } = &mut self.state
        else {
            panic!("Called in incorrect state");
        };
        let mut goto_menu = false;
        let mut settings_changed=false;
        let stopped = netman.stopped.load(Ordering::Relaxed);
        let accept_local = netman.accept_local.load(Ordering::Relaxed);
        let local_connected = netman.local_connected.load(Ordering::Relaxed);
        egui::TopBottomPanel::bottom("noita_status").show(ctx, |ui| {
            ui.add_space(3.0);
            if accept_local {
                if local_connected {
                    ui.colored_label(Color32::GREEN, tr("noita_connected"));
                } else {
                    ui.colored_label(Color32::YELLOW, tr("noita_can_connect"));
                }
            } else {
                ui.label(tr("noita_not_yet"));
            }
        });
        egui::SidePanel::left("players")
            .resizable(false)
            .exact_width(230.0)
            .show(ctx, |ui| {
                ui.add_space(3.0);
                show_player_list(ui, netman);
            });
        let mut update_lobby_data = false;
        egui::CentralPanel::default().show(ctx, |ui| {
            {
                let session=netman.session_rules.lock().unwrap();let active=netman.settings.lock().unwrap();
                ui.label(format!("Current host mode: {}",active.game_mode.unwrap_or(DefaultSettings::default().game_mode)));
                if let Some(error)=&session.failure{ui.colored_label(Color32::LIGHT_RED,error);}
                else if session.legacy_unverified {ui.colored_label(Color32::YELLOW,"Legacy run: historical rules are unverified. The original saved proxy preferences remain locked for Continue.");}
                else if session.game_received {ui.colored_label(Color32::GREEN,"Game-received health mode and proxy run identity agree.");}
                else if session.locked {ui.colored_label(Color32::YELLOW,"Proxy host settings locked/sent; waiting for the actual Noita world boundary.");}
                else {ui.label("Host can still change the first game's rules. Clients wait until the host's actual Noita world agrees.");}
            }
            if let Some(error)=&self.settings_save_error{ui.colored_label(Color32::LIGHT_RED,error);}
            ui.horizontal(|ui| {
                let last = self.connected_menu;
                ui.selectable_value(&mut self.connected_menu, ConnectedMenu::Normal, "Lobby");
                ui.selectable_value(
                    &mut self.connected_menu,
                    ConnectedMenu::Settings,
                    "Game Settings",
                );
                ui.selectable_value(
                    &mut self.connected_menu,
                    ConnectedMenu::VoIP,
                    "VoIP Settings",
                );
                ui.selectable_value(&mut self.connected_menu, ConnectedMenu::Map, "Chunk Map");
                ui.selectable_value(
                    &mut self.connected_menu,
                    ConnectedMenu::NoitaLog,
                    "Noita Log",
                );
                ui.selectable_value(
                    &mut self.connected_menu,
                    ConnectedMenu::ProxyLog,
                    "Proxy Log",
                );
                if netman.peer.is_steam() {
                    ui.selectable_value(
                        &mut self.connected_menu,
                        ConnectedMenu::ConnectionInfo,
                        "Connection Info",
                    );
                }
                if !netman.ban_list.lock().unwrap().is_empty() {
                    ui.selectable_value(
                        &mut self.connected_menu,
                        ConnectedMenu::BanList,
                        "Ban List",
                    );
                }
                if !netman.active_mods.lock().unwrap().is_empty() {
                    ui.selectable_value(&mut self.connected_menu, ConnectedMenu::Mods, "Mod List");
                }
                if last == ConnectedMenu::Settings && last != self.connected_menu {
                    if netman.peer.is_host(){netman.apply_host_preferences(&self.app_saved_state.game_settings);update_lobby_data=true;}
                }
                ui.add_space(ui.available_width() - 56.0);
                if ui.button("Back out").clicked() {
                    goto_menu = true
                }
            });
            ui.separator();
            if stopped {
                ui.colored_label(Color32::LIGHT_RED, "Netmanager thread has stopped");
                if let Some(err) = netman.error.lock().unwrap_or_else(|poison|poison.into_inner()).as_ref() {
                    ui.label("With the following error:");
                    ui.label(err.to_string());
                }
                ui.separator();
            }
            if let Some(game) = self.paths.noita_exe().parent() {
                self.noitalog.poll_if_due(&game.join("logger.txt"));
            }
            let proxy_log_path = std::env::current_exe().ok()
                .and_then(|path|path.parent().map(|p|p.join(paths::DEFAULT_PROXY_LOG_NAME)))
                .unwrap_or_else(||paths::DEFAULT_PROXY_LOG_NAME.into());
            self.proxylog.poll_if_due(&proxy_log_path);
            match self.connected_menu {
                ConnectedMenu::Normal => {
                    if netman.peer.is_steam() {
                        if let Some(id) = netman.peer.lobby_id() {
                            if ui.button(tr("netman_save_lobby")).clicked() || !self.copied_lobby {
                                let lobby_code = LobbyCode {
                                    kind: self.my_lobby_kind,
                                    code: id,
                                };
                                if let Some(clipboard) = self.clipboard.as_mut() {
                                    let _ = clipboard.set_text(lobby_code.serialize());
                                }
                                self.copied_lobby = true;
                            }
                        } else {
                            ui.label("No lobby created yet");
                        }
                    }
                    self.appearance.mina_color_picker(
                        ui,
                        self.paths.noita_save.clone(),
                        self.player_image.clone(),
                    );
                    ui.add_space(15.0);
                    ui.horizontal(|ui| {
                        if ui.button("save colors").clicked() {
                            let desc = self
                                .appearance
                                .create_png_desc(self.paths.noita_save.clone());
                            netman.new_desc(desc, self.player_image.clone());
                            *netman.new_desc.lock().unwrap() = Some(desc);
                        };
                        ui.label("requires noita restart")
                    });
                    ui.add_space(15.0);
                    if accept_local && !local_connected {
                        match noita_launcher.launch_token() {
                            LaunchTokenResult::Ok(mut token) => {
                                let start_auto = self.can_start_automatically
                                    && self.app_saved_state.start_game_automatically;
                                if start_auto || ui.button(tr("launcher_start_game")).clicked() {
                                    if netman.prepare_game_launch().is_err(){return;}
                                    info!("Starting the game now");
                                    token.start_game(
                                        netman.actual_noita_port.load(Ordering::Relaxed),
                                    );
                                    self.can_start_automatically = false;
                                }
                            }
                            LaunchTokenResult::AlreadyStarted => {
                                ui.label(tr("launcher_already_started"));
                            }
                            LaunchTokenResult::CantStart => {
                                ui.label(tr("launcher_no_command"));
                                ui.label(tr("launcher_no_command_2"));
                                ui.label(tr("launcher_no_command_3"));
                            }
                        }
                    } else {
                        ui.label(tr("launcher_only_when_awaiting"));
                    }
                    if netman.peer.is_host() {
                        ui.add_space(15.0);
                        self.end_run_button.show(ui, netman);
                        ui.add_space(15.0);
                        {
                            let mut temp = netman.no_more_players.load(Ordering::Relaxed);
                            if ui
                                .checkbox(&mut temp, "don't let more players join")
                                .changed()
                            {
                                netman.no_more_players.store(temp, Ordering::Relaxed);
                            }
                        }
                        {
                            let mut temp = netman.no_chunkmap_to_players.load(Ordering::Relaxed);
                            if ui
                                .checkbox(&mut temp, "don't send chunk map to players")
                                .changed()
                            {
                                netman.no_chunkmap_to_players.store(temp, Ordering::Relaxed);
                            }
                        }
                        {
                            let mut temp = netman.no_chunkmap.load(Ordering::Relaxed);
                            if ui.checkbox(&mut temp, "don't save chunk map, chunkmap is disabled by default do to current implementation ram/vram leaking on long runs").changed() {
                                netman.no_chunkmap.store(temp, Ordering::Relaxed);
                            }
                        }
                    }
                }
                ConnectedMenu::Mods => {
                    let mods_list = netman.active_mods.lock().unwrap();
                    for mods in mods_list.iter() {
                        ui.label(mods);
                    }
                    if mods_list.is_empty() {
                        self.connected_menu = ConnectedMenu::Normal
                    }
                }
                ConnectedMenu::Map => self.map.ui(ui, netman, ctx),
                ConnectedMenu::BanList => {
                    let mut ban_list = netman.ban_list.lock().unwrap();
                    let mut i = ban_list.len();
                    while i != 0 {
                        i -= 1;
                        if ui.button(format!("unban {}", ban_list[i])).clicked() {
                            ban_list.remove(i);
                        }
                    }
                    if ban_list.is_empty() {
                        self.connected_menu = ConnectedMenu::Normal
                    }
                }
                ConnectedMenu::Settings => {
                    ScrollArea::vertical().auto_shrink(false).show(ui, |ui| {
                        if netman.peer.is_host() {
                            let locked=netman.session_rules.lock().unwrap().locked;
                            ui.label(if locked{"Next run: edits below are queued until the run ends. Restarting the proxy or choosing Noita New Game does not apply them."}else{"First game: changes below apply now. No restart or end-run is required."});
                            if self.app_saved_state.game_settings.show_editor(ui, true){netman.apply_host_preferences(&self.app_saved_state.game_settings);update_lobby_data=true;settings_changed=true;}
                        } else {
                            netman.settings.lock().unwrap().show_editor(ui, false);
                        }
                    });
                }
                ConnectedMenu::ConnectionInfo => match &netman.peer {
                    PeerVariant::Tangled(_) => {
                        ui.label("No connection info available in tangled mode");
                    }
                    PeerVariant::Steam(peer) => {
                        let steam = self.steam_state.as_ref().unwrap();
                        let report = peer.generate_report();
                        egui::Grid::new("Conn status grid")
                            .striped(true)
                            .show(ui, |ui| {
                                add_per_status_ui(&report, steam, ui);
                            });
                        ctx.request_repaint_after(Duration::from_millis(16));
                    }
                },
                ConnectedMenu::VoIP => {
                    let mut save = self.audio.show_ui(ui, false);
                    for peer in netman.peer.iter_peer_ids() {
                        if netman.peer.my_id() != peer {
                            ui.label(format!(
                                "volume for {}",
                                netman
                                    .nicknames
                                    .lock()
                                    .unwrap()
                                    .get(&peer)
                                    .unwrap_or(&peer.to_string())
                            ));
                            if ui
                                .add(Slider::new(
                                    self.audio.volume.entry(peer).or_insert(1.0),
                                    0.0..=8.0,
                                ))
                                .changed()
                            {
                                save = true;
                            }
                        }
                    }
                    if save {
                        *netman.audio.lock().unwrap() = self.audio.clone()
                    }
                }
                ConnectedMenu::NoitaLog => {
                    show_log(ui, &self.noitalog, &mut self.clipboard);
                }
                ConnectedMenu::ProxyLog => {
                    show_log(ui, &self.proxylog, &mut self.clipboard);
                }
            }
            if self.app_saved_state.show_extra_debug_stuff {
                ui.checkbox(
                    &mut self.app_saved_state.record_all,
                    tr("Record-everything-sent-to-noita"),
                );
            }
        });
        goto_menu |= netman.back_out.load(Ordering::Relaxed);
        netman
            .enable_recorder
            .store(self.app_saved_state.record_all, Ordering::Relaxed);
        if update_lobby_data {
            let data = self.make_lobby_extra_data();
            if let AppState::ConnectedLobby { netman, .. } = &mut self.state {
                netman.update_lobby_data(data);
            }
        }
        if goto_menu {
            self.state = AppState::Connect;
        }
        if settings_changed{self.set_settings();}
    }
}

impl eframe::App for App {
    fn update(&mut self, ctx: &Context, _frame: &mut eframe::Frame) {
        if !self.user_close_reported && ctx.input(|input|input.viewport().close_requested()) {
            self.user_close_reported=true;
            crate::startup_diagnostics::event("user-close-request","proxy window close requested");
        }
        ctx.request_repaint_after(Duration::from_millis(500));
        match &mut self.state {
            AppState::Connect => {
                self.connect_screen(ctx);
            }
            AppState::ConnectedLobby {
                netman,
                noita_launcher: _,
            } => {
                if let steam_networking::ExtraPeerState::CouldNotConnect(err) = netman.peer.state()
                {
                    self.notify_error(err);
                    return;
                }
                self.show_lobby(ctx);
            }
            AppState::Error { message } => {
                let add_contents = |ui: &mut Ui| {
                    ui.heading(tr("error_occured"));
                    ui.label(&*message);
                    ui.button(tr("button_back")).clicked()
                };
                if egui::CentralPanel::default().show(ctx, add_contents).inner {
                    self.state = AppState::Connect;
                }
            }
            AppState::ModManager => {
                egui::CentralPanel::default().show(ctx, draw_bg);
                Window::new(tr("modman"))
                    .auto_sized()
                    .anchor(Align2::CENTER_CENTER, [0.0, 0.0])
                    .show(ctx, |ui| {
                        ui.set_max_width(600.0);
                        self.modmanager.update(
                            ctx,
                            ui,
                            &mut self.paths,
                            self.steam_state.as_mut().ok(),
                            &self.args,
                        )
                    });
                if self.modmanager.is_done() {
                    self.switch_to_connect();
                }
            }
            AppState::SelfUpdate => {
                egui::CentralPanel::default().show(ctx, draw_bg);
                Window::new(tr("selfupdate"))
                    .auto_sized()
                    .anchor(Align2::CENTER_CENTER, [0.0, 0.0])
                    .show(ctx, |ui| {
                        ui.set_max_width(600.0);
                        self.self_update.self_update(ui);
                    });
            }
            AppState::LangPick => {
                if let Some(lang) = &self.args.language {
                    let li: LanguageIdentifier = lang.parse().unwrap();
                    self.app_saved_state.lang_id = Some(li.clone());
                    set_current_locale(li);
                    self.state = AppState::ModManager;
                    return;
                }
                egui::CentralPanel::default().show(ctx, draw_bg);
                Window::new(tr("lang_picker"))
                    .auto_sized()
                    .anchor(Align2::CENTER_CENTER, [0.0, 0.0])
                    .show(ctx, |ui| {
                        for lang in &LANGS {
                            ui.set_max_width(200.0);
                            ui.vertical_centered_justified(|ui| {
                                if ui.button(lang.name()).clicked() {
                                    self.app_saved_state.lang_id = Some(lang.id());
                                    set_current_locale(lang.id())
                                }
                            });
                        }
                        if ui.button(tr("button_confirm")).clicked() {
                            self.state = AppState::ModManager;
                        }
                    });
            }
            AppState::AskSavestateReset => {
                Window::new(tr("An-in-progress-run-has-been-detected"))
                    .auto_sized()
                    .anchor(Align2::CENTER_CENTER, [0.0, 0.0])
                    .show(ctx, |ui| {
                        ui.label(tr("savestate_desc"));
                        if self.run_save_state.load_checked::<RunInfo>().ok().flatten().is_some_and(|info|SavedRunSettings::load(self.run_save_state.directory(),info.seed).ok().flatten().is_none()) {
                            ui.colored_label(Color32::YELLOW,format!("Legacy checkpoint: historical proxy rules were not recorded. Continue keeps this startup's saved proxy preferences ({}) and remains unverified; it will not apply newly queued edits. Confirm these were the run's rules before continuing.",self.legacy_startup_settings.game_mode.unwrap_or(DefaultSettings::default().game_mode)));
                        }
                        ui.label("Preparing a new proxy run keeps the old proxy checkpoint until an actual new Noita world agrees. Noita New Game and proxy Continue are separate choices; save both PCs before switching.");
                        ui.horizontal(|ui| {
                            if ui.button(tr("Continue")).clicked() {
                                self.new_run_intent=false;
                                self.state = AppState::Connect;
                            }
                            if ui.button(tr("New-game")).clicked() {
                                self.new_run_intent=true;
                                self.state = AppState::Connect;
                            }
                        });
                    });
            }
            AppState::TangledConnecting { peer } => {
                egui::CentralPanel::default().show(ctx, |ui| {
                    ui.label(tr("ip_wait_for_connection"));
                });
                if peer.state() == tangled::PeerState::Disconnected {
                    self.state = AppState::Error {
                        message: tr("ip_could_not_connect"),
                    };
                    return;
                }
                if peer.my_id().is_some() {
                    let AppState::TangledConnecting { peer } =
                        mem::replace(&mut self.state, AppState::Connect)
                    else {
                        unreachable!();
                    };
                    self.start_connect_step_2(peer);
                }
            }
            AppState::GogModeIssue(target_lobby) => {
                let mut button_back = false;
                let mut button_restart = false;
                egui::CentralPanel::default().show(ctx, |ui| {
                    ui.label(format!(
                        "Mismathing modes: Host is in {:?} mode, you're in {:?} mode",
                        target_lobby.kind, self.my_lobby_kind
                    ));
                    if crate::frozen_runtime::ID.is_some() && target_lobby.kind!=LobbyKind::Steam {ui.colored_label(Color32::LIGHT_RED,"This frozen Co-op+ runtime requires a matching Steam lobby. Switching to GOG mode is unsupported; settings and saved runs were retained.");}
                    else {button_restart = ui
                        .add(Button::new(tr("Switch-mode-and-restart")).fill(Color32::RED))
                        .clicked();}
                    button_back = ui.button(tr("button_back")).clicked();
                });
                if button_restart {
                    let Err(err) = SelfRestarter::new().and_then(|mut r| {
                        r.override_lobby_kind(target_lobby.kind)
                            .connect_to(*target_lobby)
                            .restart()
                    });
                    self.notify_error(format!("Failed to self-restart: {err}"));
                }
                if button_back {
                    self.state = AppState::Connect;
                }
            }
        };
    }
    fn on_exit(&mut self, _: Option<&eframe::glow::Context>) {
        self.set_settings();
    }
}

#[derive(Default)]
struct EndRunButton {
    end_run_confirmation: bool,
}

impl EndRunButton {
    fn show(&mut self, ui: &mut Ui, netman: &mut NetManStopOnDrop) {
        ui.horizontal(|ui| {
            let dirty = netman.dirty.load(Ordering::Relaxed);
            let button = Button::new(tr("launcher_end_run"))
                .small()
                .fill(Color32::LIGHT_RED);
            if !self.end_run_confirmation
                && if dirty {
                    ui.add(button).clicked()
                } else {
                    ui.button(tr("launcher_end_run")).clicked()
                }
            {
                self.end_run_confirmation = true
            } else if self.end_run_confirmation
                && ui.button(tr("launcher_end_run_confirm")).clicked()
            {
                self.end_run_confirmation = false;
                netman.end_run.store(true, Ordering::Relaxed);
            };
            if dirty {
                ui.label("PENDING SETTINGS NOT SET UNTIL RUN ENDS");
            }
        });
    }
}

fn filled_group<R>(ui: &mut Ui, add_contents: impl FnOnce(&mut Ui) -> R) -> InnerResponse<R> {
    let style = ui.style();
    let frame = egui::Frame {
        inner_margin: Margin::same(6), // same and symmetric looks best in corners when nesting groups
        stroke: style.visuals.widgets.noninteractive.bg_stroke,
        fill: Color32::from_rgba_premultiplied(20, 20, 20, 180),
        ..Default::default()
    };
    frame.show(ui, add_contents)
}

fn heading_with_underline(ui: &mut Ui, text: impl Into<RichText>) {
    ui.vertical_centered_justified(|ui| {
        ui.heading(text);
    });
    ui.separator();
}

fn square_button_text(ui: &mut Ui, text: &str, size: f32) -> egui::Response {
    let side = ui.available_width();
    ui.add_sized([side, side], Button::new(RichText::new(text).size(size)))
}

fn square_button_icon(ui: &mut Ui, icon: egui::Image) -> egui::Response {
    let side = ui.available_width();
    ui.add_sized(
        [side, side],
        ImageButton::new(icon), // Somewhy it doesnt inherit style correctly
    )
}

fn draw_bg(ui: &mut Ui) {
    let image = egui::Image::new(egui::include_image!("../assets/noita_ew_logo_sq.webp"))
        .texture_options(TextureOptions::NEAREST);

    let rect = ui.ctx().screen_rect();
    let aspect_ratio = 1.0;
    let new_height = f32::max(rect.width() * aspect_ratio, rect.height());
    let new_width = new_height / aspect_ratio;
    let rect = Rect::from_center_size(rect.center(), Vec2::new(new_width, new_height));

    image.paint_at(ui, rect);
}

fn show_player_list(ui: &mut Ui, netman: &mut NetManStopOnDrop) {
    ScrollArea::vertical().auto_shrink(false).show(ui, |ui| {
        let nicknames = netman.nicknames.lock().unwrap().clone();
        let minas = netman.minas.lock().unwrap().clone();
        for peer in netman.peer.iter_peer_ids().clone() {
            let role = peer_role(peer, netman);
            let peer_str = peer.to_string().clone();
            let username = nicknames.get(&peer).unwrap_or(&peer_str);
            let mina = minas.get(&peer);
            ui.group(|ui| {
                if let Some(img) = mina {
                    display_with_labels(img.clone(), ui, username, &role, netman, peer)
                } else {
                    ui.label(username);
                    if peer != netman.peer.my_id() {
                        ui.horizontal(|ui| {
                            if mina.is_some() {
                                ui.add_space(5.0);
                            }
                            if netman.peer.is_host() {
                                if ui.button("Kick").clicked() {
                                    netman.kick_list.lock().unwrap().push(peer)
                                }
                                if ui.button("Ban").clicked() {
                                    netman.ban_list.lock().unwrap().push(peer)
                                }
                            }
                            if ui.button("Mods").clicked() {
                                netman.send(peer, &NetMsg::RequestMods, Reliability::Reliable);
                            }
                        });
                    }
                }
            });
        }
    });
}

fn display_with_labels(
    img: RgbaImage,
    ui: &mut Ui,
    label_top: &str,
    label_bottom: &str,
    netman: &mut NetManStopOnDrop,
    peer: OmniPeerId,
) {
    ui.scope(|ui| {
        ui.set_min_width(200.0);
        ui.horizontal(|ui| {
            display_player_skin(ui, img, 4.0);
            ui.vertical(|ui| {
                if netman.peer.my_id() != peer {
                    ui.horizontal(|ui| {
                        if netman.peer.is_host() {
                            if ui.button("Kick").clicked() {
                                netman.kick_list.lock().unwrap().push(peer)
                            }
                            if ui.button("Ban").clicked() {
                                netman.ban_list.lock().unwrap().push(peer)
                            }
                        }
                        if ui.button("Mods").clicked() {
                            netman.send(peer, &NetMsg::RequestMods, Reliability::Reliable);
                        }
                    });
                }
                ui.label(RichText::new(label_top).size(14.0));
                ui.label(RichText::new(label_bottom).size(11.0));
            });
        })
    });
}
fn add_per_status_ui(
    report: &steam_networking::ConnectionStatusReport,
    steam: &steam_helper::SteamState,
    ui: &mut Ui,
) {
    ui.label("Name");
    ui.label("Status");
    ui.label("Ping");
    ui.label("LocQ❓")
        .on_hover_text("Local Connection Quality (percentage of packets we delivered).");
    ui.label("RemQ❓")
        .on_hover_text("Remote Connection Quality (percentage of packets delivered to us).");
    ui.label("In");
    ui.label("Out");
    ui.label("MaxSendRate");
    ui.label("PenUnr❓")
        .on_hover_text("Pending unreliable messages");
    ui.label("PenRel❓")
        .on_hover_text("Pending reliable messages");
    ui.label("UnAck❓").on_hover_text(
        "Amount of reliable packages that were sent but weren't confirmed as received yet.",
    );
    ui.end_row();

    for steam_networking::PerPeerStatusEntry { peer, status } in &report.per_peer_statuses {
        let name = steam.get_user_name((*peer).into());
        ui.label(&name);
        match status {
            steam_networking::PerPeerStatus::Connected { realtimeinfo } => {
                ui.label("Ok❓").on_hover_text("Connected");
                ui.label(format!("{}ms", realtimeinfo.ping()));
                ui.label(format!(
                    "{:.2}%",
                    realtimeinfo.connection_quality_local() * 100.0
                ));
                ui.label(format!(
                    "{:.2}%",
                    realtimeinfo.connection_quality_remote() * 100.0
                ));
                ui.label(format!("{}by/s", realtimeinfo.in_bytes_per_sec()));
                ui.label(format!("{}by/s", realtimeinfo.out_bytes_per_sec()));
                ui.label(format!("{}by/s", realtimeinfo.send_rate_bytes_per_sec()));
                ui.label(format!("{}", realtimeinfo.pending_unreliable()));
                ui.label(format!("{}", realtimeinfo.pending_reliable()));
                ui.label(format!("{}", realtimeinfo.sent_unacked_reliable()));
            }
            steam_networking::PerPeerStatus::AwaitingIncoming => {
                ui.label("Awa❓")
                    .on_hover_text("Awaiting incoming connection from this peer.");
            }
            steam_networking::PerPeerStatus::ConnectionPending => {
                ui.label("Pen❓").on_hover_text("Connection pending.");
            }
            steam_networking::PerPeerStatus::NoFurtherInfo => {
                ui.label("NoI❓")
                    .on_hover_text("Connected, but no further info available.");
            }
        }
        ui.end_row();
    }
}

fn peer_role(peer: OmniPeerId, netman: &Arc<NetManager>) -> String {
    if peer == netman.peer.host_id() {
        tr("player_host")
    } else if peer == netman.peer.my_id() {
        tr("player_me")
    } else {
        tr("player_player")
    }
}

#[cfg(test)]
mod log_ui_tests {
    use super::*;
    use std::{fs, io::Write, path::PathBuf, sync::atomic::{AtomicU64, Ordering as AtomicOrdering}};
    use egui::{CentralPanel, RawInput, epaint::Shape};

    static NEXT: AtomicU64 = AtomicU64::new(0);
    struct LogFixture { dir: PathBuf, path: PathBuf }
    impl LogFixture {
        fn new(bytes: &[u8]) -> Self {
            let dir = std::env::temp_dir().join(format!("ew-log-ui-{}-{}", std::process::id(), NEXT.fetch_add(1, AtomicOrdering::Relaxed)));
            fs::create_dir_all(&dir).unwrap();
            let path = dir.join("logger.txt");fs::write(&path, bytes).unwrap();
            Self {dir, path}
        }
    }
    impl Drop for LogFixture {
        fn drop(&mut self) {
            if let (Ok(dir), Ok(root)) = (self.dir.canonicalize(), std::env::temp_dir().canonicalize()) {
                if dir != root && dir.starts_with(&root) && dir.file_name() == self.dir.file_name()
                    && dir.file_name().is_some_and(|name|name.to_string_lossy().starts_with("ew-log-ui-")) {
                    let _ = fs::remove_dir_all(dir);
                }
            }
        }
    }

    #[derive(Default)]
    struct Painted { texts: Vec<String>, log_rows: usize, layout_rows: usize, shapes: usize, communication_header: Option<Rect> }
    fn inspect(shape: &Shape, clip: Rect, painted: &mut Painted) {
        painted.shapes += 1;
        match shape {
            Shape::Vec(shapes) => for shape in shapes {inspect(shape, clip, painted)},
            Shape::Text(text) => {
                let s = text.galley.text();
                painted.layout_rows += text.galley.rows.len();
                if text.visual_bounding_rect().intersects(clip) {
                    if s.starts_with("First specific communication failure observed") {
                        painted.communication_header = Some(text.visual_bounding_rect().intersect(clip));
                    }
                    painted.log_rows += s.lines().filter(|line|line.starts_with("logrow ")).count();
                    painted.texts.push(s.to_owned());
                }
            }
            _ => {}
        }
    }
    fn render(ctx: &Context, log: &LogReader, frame: usize, events: Vec<egui::Event>) -> Painted {
        let output = ctx.run(RawInput {
            screen_rect: Some(Rect::from_min_size(egui::Pos2::ZERO, Vec2::new(900.0,700.0))),
            time: Some(frame as f64 / 60.0), events, focused: true, ..RawInput::default()
        }, |ctx| {
            CentralPanel::default().show(ctx, |ui| {
                show_log(ui, log, &mut None);
            });
        });
        let mut painted = Painted::default();
        let viewport = Rect::from_min_size(egui::Pos2::ZERO, Vec2::new(900.0,700.0));
        for shape in output.shapes {inspect(&shape.shape, shape.clip_rect.intersect(viewport), &mut painted);}
        painted
    }

    #[test]
    fn headless_log_view_virtualizes_history_and_paints_retention_limits() {
        let mut bytes = Vec::new();
        for row in 0..30_000 {writeln!(bytes,"logrow {row:05}: unique ordinary diagnostic message").unwrap();}
        let fixture = LogFixture::new(&bytes);let mut reader = LogReader::default();
        for _ in 0..2000 {reader.poll(&fixture.path);if !reader.catching_up(){break;}}
        assert!(!reader.catching_up());assert!(reader.row_count() >= 4000);
        let ctx = Context::default();ctx.style_mut(|style|style.animation_time=0.0);
        let _ = render(&ctx,&reader,0,vec![]);let painted = render(&ctx,&reader,1,vec![]);
        let cached_galleys = ctx.fonts(|fonts|fonts.num_galleys_in_cache());
        let text = painted.texts.join("\n");
        assert!(text.contains("256 KiB / 4096 lines"));assert!(text.contains("Earlier runs are not retained"));
        assert!(painted.log_rows > 0 && painted.log_rows < 80,"painted {} history rows",painted.log_rows);
        assert!(painted.layout_rows < 256,"laid out {} paint rows",painted.layout_rows);
        assert!(cached_galleys < 256,"cached {cached_galleys} unique text layouts for a fixed viewport");
        assert!(painted.shapes < 256);
        eprintln!("log_ui viewport=900x700 source_bytes={} retained_rows={} painted_log_rows={} painted_layout_rows={} cached_galleys={} shapes={}",bytes.len(),reader.row_count(),painted.log_rows,painted.layout_rows,cached_galleys,painted.shapes);
    }

    #[test]
    fn headless_log_view_paints_pending_scan_and_provisional_context() {
        let mut bytes = b"ERROR - LoadSpriteTo() data/temp/temp_gun.png\n".to_vec();
        for row in 0..30_000 {writeln!(bytes,"logrow {row:05}: unique ordinary diagnostic message").unwrap();}
        bytes.extend_from_slice(b"Error in rust call: Couldn't receive the length from stream\nuseful transport trace\n");
        let fixture = LogFixture::new(&bytes);let mut reader = LogReader::default();
        for _ in 0..4 {reader.poll(&fixture.path);}
        assert!(reader.catching_up());assert!(!reader.first_communication_failure.as_ref().unwrap().earlier_scanned);
        let ctx = Context::default();ctx.style_mut(|style|style.animation_time=0.0);
        let first = render(&ctx,&reader,0,vec![]);
        let header = first.communication_header.expect("communication header must be painted inside the viewport");
        let pointer = egui::Pos2::new(header.left()+8.0,header.center().y);
        let button = |pressed|egui::Event::PointerButton{pos:pointer,button:egui::PointerButton::Primary,pressed,modifiers:egui::Modifiers::default()};
        // Negative control reproduces the previous fixture's one-second hold.
        // egui defaults max_click_duration to 0.8 s, so it must not be a click.
        let _ = render(&ctx,&reader,60,vec![egui::Event::PointerMoved(pointer),button(true)]);
        let held_release = render(&ctx,&reader,120,vec![button(false)]);
        assert!(!ctx.input(|input|input.pointer.primary_clicked()));
        assert!(!held_release.texts.join("\n").contains("Provisional: earlier bytes have not yet been verified"));
        // A real short click follows the exact same painted-header coordinates.
        let _ = render(&ctx,&reader,121,vec![button(true)]);
        let _ = render(&ctx,&reader,122,vec![button(false)]);
        assert!(ctx.input(|input|input.pointer.primary_clicked()),"the headless input must generate a short click");
        let painted = render(&ctx,&reader,180,vec![]);
        let text = painted.texts.join("\n");
        assert!(text.contains("scanning for the first failure in bounded steps"));
        assert!(text.contains("Provisional: earlier bytes have not yet been verified"),"Painted text after real header click:\n{text}");
        assert!(text.contains("Couldn't receive the length from stream"));
        assert!(text.contains("16 KiB / 9 lines each"));
        assert!(painted.layout_rows < 256 && painted.shapes < 256);
        eprintln!("log_ui header_rect={header:?} pointer={pointer:?} long_hold_clicked=false short_16ms_clicked=true provisional=true pending_scan=true transport_context_painted=true layout_rows={} shapes={}",painted.layout_rows,painted.shapes);
    }

    #[derive(Default)]
    struct NarrowPaint {
        general: Option<Rect>, communication: Option<Rect>,
        starts: Vec<Rect>, ends: Vec<Rect>, text_ends: Vec<(String,usize)>,
    }
    impl NarrowPaint {
        fn visible_end(&self, text: &str) -> usize {
            self.text_ends.iter().filter(|(s,_)|s==text).map(|(_,end)|*end).max().unwrap_or(0)
        }
        fn latest_visible(&self) -> bool {self.visible_end("LATEST")==6}
    }
    fn inspect_narrow(shape: &Shape, clip: Rect, out: &mut NarrowPaint) {
        match shape {
            Shape::Vec(shapes) => for shape in shapes {inspect_narrow(shape,clip,out)},
            Shape::Text(text) => {
                let s = text.galley.text();let mut index=0;let mut visible_end=0;
                for row in &text.galley.rows {
                    for glyph in &row.glyphs {
                        index+=1;
                        if clip.contains(glyph.logical_rect().translate(text.pos.to_vec2()).center()) {visible_end=index;}
                    }
                    if row.ends_with_newline {index+=1;}
                }
                if visible_end>0 {
                    let rect=text.visual_bounding_rect().intersect(clip);
                    if s.starts_with("First failure-like context") {out.general=Some(rect);}
                    if s.starts_with("First specific communication failure observed") {out.communication=Some(rect);}
                    if s=="Start" {out.starts.push(rect);}
                    if s=="End" {out.ends.push(rect);}
                    out.text_ends.push((s.to_owned(),visible_end));
                }
            }
            _=>{}
        }
    }
    fn render_narrow(ctx: &Context, log: &LogReader, frame: usize, events: Vec<egui::Event>) -> NarrowPaint {
        let viewport=Rect::from_min_size(egui::Pos2::ZERO,Vec2::new(800.0,600.0));
        let output=ctx.run(RawInput{screen_rect:Some(viewport),time:Some(frame as f64/60.0),events,focused:true,..RawInput::default()},|ctx|{
            // Same status/sidebar dimensions and tab-row height as show_lobby,
            // without constructing a Steam connection or operating the desktop.
            egui::TopBottomPanel::bottom("noita_status").show(ctx,|ui|{ui.add_space(3.0);ui.label("Noita connected");});
            egui::SidePanel::left("players").resizable(false).exact_width(230.0).show(ctx,|ui|{ui.label("Players");});
            CentralPanel::default().show(ctx,|ui|{
                ui.horizontal(|ui|{
                    for title in ["Lobby","Game Settings","VoIP Settings","Chunk Map","Noita Log","Proxy Log"] {let _=ui.selectable_label(title=="Noita Log",title);}
                });ui.separator();show_log(ui,log,&mut None);
            });
        });
        let mut out=NarrowPaint::default();
        for shape in output.shapes {inspect_narrow(&shape.shape,shape.clip_rect.intersect(viewport),&mut out);}
        out
    }
    fn click_narrow(ctx: &Context, log: &LogReader, frame: &mut usize, rect: Rect) -> NarrowPaint {
        let p=rect.center();let button=|pressed|egui::Event::PointerButton{pos:p,button:egui::PointerButton::Primary,pressed,modifiers:egui::Modifiers::default()};
        *frame+=1;let _=render_narrow(ctx,log,*frame,vec![egui::Event::PointerMoved(p),button(true)]);
        *frame+=1;let _=render_narrow(ctx,log,*frame,vec![button(false)]);
        assert!(ctx.input(|i|i.pointer.primary_clicked()),"fixture must deliver a short actual widget click");
        *frame+=60;let _=render_narrow(ctx,log,*frame,vec![]);
        *frame+=1;render_narrow(ctx,log,*frame,vec![])
    }
    fn exercise_expanded_contexts(both_maximum: bool, read_error: bool) {
        let first=format!("ERROR: {}end","trace ".repeat(681));let other=format!("{}done","trace ".repeat(682));
        assert_eq!(first.len(),4096);assert_eq!(other.len(),4096);
        let mut bytes=format!("{first}\n{}Couldn't receive length from stream\n",format!("{other}\n").repeat(3)).into_bytes();
        if both_maximum {bytes.extend_from_slice(format!("{}\n",other).repeat(4).as_bytes());}
        bytes.extend_from_slice(b"LATEST\n");let fixture=LogFixture::new(&bytes);let mut log=LogReader::default();
        for _ in 0..20 {log.poll(&fixture.path);if !log.catching_up(){break;}}
        if read_error {log.error=Some(format!("Cannot read {}: Access is denied. Showing the last captured content.",fixture.path.display()));}
        let general=log.first_failure.as_ref().unwrap().text.clone();let communication=log.first_communication_failure.as_ref().unwrap().text.clone();
        assert!(general.len()>=16_380);if both_maximum {assert!(communication.len()>=16_380);}
        let ctx=Context::default();ctx.style_mut(|s|s.animation_time=0.0);let mut frame=0;
        let _=render_narrow(&ctx,&log,frame,vec![]);frame+=1;let initial=render_narrow(&ctx,&log,frame,vec![]);
        let expanded_general=click_narrow(&ctx,&log,&mut frame,initial.general.expect("general header must be visible initially"));
        assert!(expanded_general.communication.is_some(),"expanding the general context hid the communication header outside800x600/sidebar230");
        let mut both=click_narrow(&ctx,&log,&mut frame,expanded_general.communication.unwrap());
        assert!(both.latest_visible(),"LATEST row must remain visible with both contexts expanded");
        assert!(both.text_ends.iter().any(|(s,_)|s=="Copy partial capture"),"Copy control must remain visible, disabled when clipboard unavailable");
        assert_eq!(both.ends.len(),2,"both contexts need an accessible End control");
        let comm_before=both.visible_end(&communication);
        both=click_narrow(&ctx,&log,&mut frame,both.ends[0]);
        assert_eq!(both.visible_end(&general),general.trim_end_matches('\n').chars().count(),"End of general context must actually paint its last captured glyph");
        assert_eq!(both.visible_end(&communication),comm_before,"general navigation must not scroll communication context");
        both=click_narrow(&ctx,&log,&mut frame,both.ends[1]);
        let general_end=both.visible_end(&general);let comm_end=communication.trim_end_matches('\n').chars().count();
        assert_eq!(both.visible_end(&communication),comm_end,"End of communication context must paint its last captured glyph");
        both=click_narrow(&ctx,&log,&mut frame,both.starts[0]);
        assert!(both.visible_end(&general)<general_end,"Start must return general context to its beginning");
        assert_eq!(both.visible_end(&communication),comm_end,"Start of general must preserve communication scroll offset");
        assert!(both.latest_visible());
        eprintln!("log_ui800 sidebar=230 both_maximum={both_maximum} read_error={read_error} general_bytes={} communication_bytes={} independent_end_navigation=true latest_visible=true copy_visible=true",general.len(),communication.len());
    }
    #[test] fn expanded_log_contexts_exact_user_fixture_stay_accessible() {exercise_expanded_contexts(false,false);}
    #[test] fn expanded_log_contexts_maximum_and_retained_error_stay_accessible() {exercise_expanded_contexts(true,true);}


    #[test]
    fn review_expanded_pending_contexts_with_retained_error_remain_navigable() {
        let first = format!("ERROR: {}end", "trace ".repeat(681));
        let other = format!("{}done", "trace ".repeat(682));
        assert_eq!(first.len(), 4096);
        assert_eq!(other.len(), 4096);
        let mut bytes = format!("{first}\n{}", format!("{other}\n").repeat(3)).into_bytes();
        // The tail's specific failure is real, but four front-scan polls cannot
        // yet establish it as the first such failure in the long middle history.
        bytes.extend_from_slice(&b"ordinary waiting for earlier scan\n".repeat(80_000));
        bytes.extend_from_slice(b"Couldn't receive length from stream\n");
        bytes.extend_from_slice(format!("{other}\n").repeat(4).as_bytes());
        bytes.extend_from_slice(b"LATEST\n");
        let fixture = LogFixture::new(&bytes);
        let mut log = LogReader::default();
        for _ in 0..4 { log.poll(&fixture.path); }
        assert!(log.catching_up());
        assert!(!log.first_communication_failure.as_ref().unwrap().earlier_scanned);
        // The UI state after a subsequent file-access failure retains both
        // contexts and catch-up state. This does not test OS permission setup.
        log.error = Some(format!("Cannot read {}: Access is denied. Showing the last captured content.", fixture.path.display()));
        let general = log.first_failure.as_ref().unwrap().text.clone();
        let communication = log.first_communication_failure.as_ref().unwrap().text.clone();
        assert!(general.len() >= 16_380 && communication.len() >= 16_380);
        let ctx = Context::default();
        ctx.style_mut(|style| style.animation_time = 0.0);
        let mut frame = 0;
        let _ = render_narrow(&ctx, &log, frame, vec![]);
        frame += 1;
        let initial = render_narrow(&ctx, &log, frame, vec![]);
        let general_open = click_narrow(&ctx, &log, &mut frame, initial.general.unwrap());
        assert!(general_open.communication.is_some(), "provisional communication header must remain reachable");
        let mut both = click_narrow(&ctx, &log, &mut frame, general_open.communication.unwrap());
        for required in ["Access is denied", "Reading recent output", "Provisional: earlier bytes"] {
            assert!(both.text_ends.iter().any(|(text, visible_end)| text.find(required).is_some_and(|offset| *visible_end >= text[..offset + required.len()].chars().count())), "required caption phrase was not painted inside the viewport: {required}");
        }
        assert!(both.latest_visible(), "latest row must remain visible with both maximum contexts, pending scan, provisional caption and retained access error");
        assert_eq!(both.ends.len(), 2);
        let original_comm = both.visible_end(&communication);
        both = click_narrow(&ctx, &log, &mut frame, both.ends[0]);
        let general_end = general.trim_end_matches('\n').chars().count();
        assert_eq!(both.visible_end(&general), general_end);
        assert_eq!(both.visible_end(&communication), original_comm);
        both = click_narrow(&ctx, &log, &mut frame, both.ends[1]);
        let communication_end = communication.trim_end_matches('\n').chars().count();
        assert_eq!(both.visible_end(&communication), communication_end);
        both = click_narrow(&ctx, &log, &mut frame, both.starts[0]);
        assert!(both.visible_end(&general) < general_end);
        assert_eq!(both.visible_end(&communication), communication_end);
        assert!(both.latest_visible());
        eprintln!("review_ui800 sidebar=230 both_maximum=true pending_scan=true provisional=true retained_error=true independent_end_start=true latest_visible=true");
    }

}
