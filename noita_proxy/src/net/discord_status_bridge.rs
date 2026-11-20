//! Optional, local-only presentation status. No save or peer message format changes.
use crate::discord_presence::{LocalContext, LocalStatus};
use std::time::Instant;

pub(super) fn parse(text: &str, now: Instant) -> Option<LocalStatus> {
    // Bound before splitting/allocating; never accept free-form game or player text.
    if text.len() > 96 || !text.is_ascii() { return None; }
    let mut words = text.split_ascii_whitespace();
    if words.next()? != "v1" { return None; }
    let context = match words.next()? {
        "menus" => LocalContext::Menus,
        "exploring" => LocalContext::Exploring,
        "mountain" => LocalContext::HolyMountain,
        "spectating" => LocalContext::SpectatingTeammate,
        "awaiting" => LocalContext::AwaitingRevival,
        "unknown" => { if words.next()? != "-" || words.next().is_some() { return None; }
            return Some(LocalStatus { context: None, area: None, received_at: Some(now) }); },
        _ => return None,
    };
    let token = words.next()?;
    if words.next().is_some() { return None; }
    // This is deliberately a small ordinary-route allowlist. Unknown, boss and
    // secret biome identifiers never become arbitrary Discord text, even opt-in.
    let area = match token {
        "-" => None,
        "mines" => Some("Mines"),
        "coal_pits" => Some("Coal Pits"),
        "snowy_depths" => Some("Snowy Depths"),
        "hiisi_base" => Some("Hiisi Base"),
        "jungle" => Some("Underground Jungle"),
        "vault" => Some("The Vault"),
        "temple" => Some("Temple of the Art"),
        _ => return None,
    };
    if context != LocalContext::Exploring && area.is_some() { return None; }
    Some(LocalStatus { context: Some(context), area: area.map(str::to_owned), received_at: Some(now) })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn discord_bridge_known_contexts_and_route_areas_only() {
        let now=Instant::now();
        for (token,expected) in [("menus",LocalContext::Menus),("exploring",LocalContext::Exploring),("mountain",LocalContext::HolyMountain),("spectating",LocalContext::SpectatingTeammate),("awaiting",LocalContext::AwaitingRevival)] {
            let parsed=parse(&format!("v1 {token} -"),now).unwrap();assert_eq!(parsed.context,Some(expected));assert_eq!(parsed.received_at,Some(now));assert!(parsed.area.is_none());
        }
        for token in ["mines","coal_pits","snowy_depths","hiisi_base","jungle","vault","temple"] {assert!(parse(&format!("v1 exploring {token}"),now).unwrap().area.is_some());}
        assert!(parse("v1 unknown -",now).unwrap().context.is_none());
    }
    #[test]
    fn discord_bridge_rejects_unbounded_unknown_or_misplaced_information() {
        let now=Instant::now();
        for text in ["","v2 exploring mines","v1 exploring boss_secret","v1 menus mines","v1 spectating vault","v1 awaiting temple","v1 exploring mines extra","v1 exploring naïve","v1 unknown mines"] {assert!(parse(text,now).is_none(),"{text}");}
        assert!(parse(&format!("v1 exploring {}","x".repeat(100)),now).is_none());
    }
}
