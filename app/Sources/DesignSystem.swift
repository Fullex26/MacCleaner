import SwiftUI
import AppKit

// MARK: - Design System
//
// The single home for MacCleaner's visual language ("Glass & Sparkle", v2.6).
// Views never hardcode colors, fonts, or motion curves — they reach for the
// names defined here instead. Every color is appearance-aware: routed
// through `Color.dynamic(light:dark:)`, an NSColor dynamicProvider wrapper,
// rather than a single literal that would look wrong (or just flat) in one
// of the two appearances. System appearance drives everything; there is no
// in-app theme toggle.

// MARK: - Appearance-aware color helper

extension Color {
    /// Builds a `Color` that tracks the active appearance live via NSColor's
    /// dynamicProvider — the one sanctioned way in this file to define a
    /// color that differs (or is deliberately restated) between light and
    /// dark. This is what "never a single literal that ignores appearance"
    /// means in practice: everything below funnels through this or its
    /// hex-string overload.
    static func dynamic(light: NSColor, dark: NSColor) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            let isDark = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            return isDark ? dark : light
        })
    }

    /// Convenience overload for the common case of two hex strings.
    static func dynamic(light: String, dark: String) -> Color {
        dynamic(light: NSColor(maccleanerHex: light), dark: NSColor(maccleanerHex: dark))
    }
}

private extension NSColor {
    /// Minimal `#RRGGBB` parser — no alpha channel, matching every hex
    /// literal used in this file. Falls back to opaque black on malformed
    /// input rather than crashing (defensive only; every call site here
    /// passes a literal known to be well-formed).
    convenience init(maccleanerHex hex: String) {
        var value: UInt64 = 0
        Scanner(string: hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))).scanHexInt64(&value)
        let r = Double((value & 0xFF0000) >> 16) / 255.0
        let g = Double((value & 0x00FF00) >> 8) / 255.0
        let b = Double(value & 0x0000FF) / 255.0
        self.init(srgbRed: r, green: g, blue: b, alpha: 1.0)
    }
}

// MARK: - Accent + chrome colors

extension Color {
    /// The app's one accent color — cyan. Brighter/glowing in dark mode to
    /// carry the "Pro Dark Glass" mood, deeper and more restrained in light
    /// mode where the native look stays refined.
    static let accentCyan = Color.dynamic(light: "#0E7490", dark: "#22D3EE")

    /// Second stop of the disk-usage gradient bar.
    static let accentIndigo = Color.dynamic(light: "#4338CA", dark: "#6366F1")

    /// Hairline border for `glassPanel` — barely-there in both appearances.
    static let glassHairline = Color.dynamic(
        light: NSColor.black.withAlphaComponent(0.06),
        dark: NSColor.white.withAlphaComponent(0.09)
    )

    /// Track color behind `GradientBar`'s filled portion.
    static let gradientBarTrack = Color.dynamic(
        light: NSColor.black.withAlphaComponent(0.08),
        dark: NSColor.white.withAlphaComponent(0.12)
    )

    /// Outline/text color for `ReviewBadge`.
    static let reviewAmber = Color.dynamic(light: "#B45309", dark: "#FBBF24")
}

// MARK: - Category palette

/// Fixed palette for the 23 known categories (`ALL_CATEGORIES` in
/// cleaner.py). Each entry is a light/dark hex pair — brighter, more
/// saturated in dark mode to read against glass panels; deeper/darker in
/// light mode for contrast against white. Kept as literal hex pairs (not
/// computed) so the palette reads as intentionally designed rather than
/// algorithmically generated.
private let categoryPalette: [String: (light: String, dark: String)] = [
    "xcode":      ("#0E7490", "#22D3EE"), // cyan — flagship category, echoes the accent
    "docker":     ("#1D4ED8", "#60A5FA"), // blue
    "node":       ("#15803D", "#4ADE80"), // green
    "python":     ("#A16207", "#FACC15"), // yellow
    "caches":     ("#0F766E", "#2DD4BF"), // teal
    "logs":       ("#475569", "#94A3B8"), // slate
    "homebrew":   ("#B45309", "#FBBF24"), // amber
    "go":         ("#0369A1", "#38BDF8"), // sky
    "rust":       ("#92400E", "#D97757"), // terracotta/rust
    "ruby":       ("#B91C1C", "#F87171"), // red
    "cocoapods":  ("#A21CAF", "#E879F9"), // fuchsia
    "gradle":     ("#3730A3", "#818CF8"), // indigo
    "maven":      ("#57534E", "#A8A29E"), // stone/brown
    "ai":         ("#C2410C", "#F97316"), // orange
    "ide":        ("#5B21B6", "#C4B5FD"), // light violet
    "browsers":   ("#4D7C0F", "#A3E635"), // lime
    "system":     ("#4B5563", "#9CA3AF"), // gray
    "flutter":    ("#047857", "#34D399"), // emerald
    "php":        ("#7E22CE", "#C084FC"), // purple
    "vms":        ("#52525B", "#A1A1AA"), // zinc
    "tmp":        ("#6D28D9", "#A78BFA"), // violet
    "simulators": ("#BE185D", "#F472B6"), // pink
    "leftovers":  ("#2E7A1F", "#6DDB57"), // moss — the widest open hue gap
    // (~110°) left in the wheel, roughly midway between browsers' lime
    // (~84°) and node's green (~142°); distinct from both at a glance.
]

/// Display names for the 23 known categories (`ALL_CATEGORIES` in
/// cleaner.py), kept beside `categoryPalette` above since both are
/// per-category presentation. Plain `.capitalized` on the raw kebab-case id
/// mangles acronyms and short ids into "Ai", "Ide", "Vms", "Php", "Tmp" —
/// visibly broken in the app's two most-seen surfaces, the menu bar panel
/// and the Dashboard (finding B4).
private let categoryDisplayNames: [String: String] = [
    "xcode":      "Xcode",
    "docker":     "Docker",
    "node":       "Node",
    "python":     "Python",
    "caches":     "Caches",
    "logs":       "Logs",
    "homebrew":   "Homebrew",
    "go":         "Go",
    "rust":       "Rust",
    "ruby":       "Ruby",
    "cocoapods":  "CocoaPods",
    "gradle":     "Gradle",
    "maven":      "Maven",
    "ai":         "AI",
    "ide":        "IDE",
    "browsers":   "Browsers",
    "system":     "System",
    "flutter":    "Flutter",
    "php":        "PHP",
    "vms":        "VMs",
    "tmp":        "Temp files",
    "simulators": "Simulators",
    "leftovers":  "Leftovers",
]

/// Presentation name for a category id. Known categories get an explicit
/// entry from `categoryDisplayNames` above; anything else (a future
/// category added to `ALL_CATEGORIES` before this table is updated) falls
/// back to `.capitalized`, same graceful-degradation shape as
/// `categoryColor`'s hash-derived fallback.
func categoryDisplayName(_ name: String) -> String {
    categoryDisplayNames[name] ?? name.capitalized
}

/// Stable, appearance-aware color for a category dot. The 23 known
/// categories get a hand-picked entry from `categoryPalette` above; any
/// other name (a future category added to `ALL_CATEGORIES` before this
/// palette is updated) gets a deterministic hash-derived hue instead of a
/// flat gray, so it's still visually distinct and stable across launches.
///
/// The hash must be stable across process launches (Swift's built-in
/// `Hasher` is randomly seeded per run for DoS resistance, so it is
/// deliberately NOT used here) — this uses a plain djb2 walk over the
/// name's UTF-8 bytes instead.
func categoryColor(_ name: String) -> Color {
    if let pair = categoryPalette[name] {
        return Color.dynamic(light: pair.light, dark: pair.dark)
    }

    var hash: UInt64 = 5381
    for byte in name.utf8 {
        hash = ((hash << 5) &+ hash) &+ UInt64(byte)
    }
    let hue = Double(hash % 360) / 360.0

    let dark = NSColor(hue: hue, saturation: 0.70, brightness: 0.90, alpha: 1.0)
    let light = NSColor(hue: hue, saturation: 0.80, brightness: 0.55, alpha: 1.0)
    return Color.dynamic(light: light, dark: dark)
}

// MARK: - Typography

extension Font {
    /// Big hero figures — "12.4 GB reclaimable" headers.
    static let heroNumber = Font.system(size: 26, weight: .bold, design: .rounded)

    /// Standard row/list label text.
    static let rowLabel = Font.body

    /// Small meta text (timestamps, byte counts, captions) — monospaced so
    /// digits don't jitter as values change.
    static let metaCaption = Font.system(.caption, design: .monospaced)

    /// Uppercase section-caption recipe (10pt semibold) shared by
    /// `SettingsSection`'s title, `ReviewBadge`'s text, and any other small
    /// all-caps tracked label — a single named token instead of each call
    /// site hand-inlining `.system(size: 10, weight: .semibold)`.
    static let sectionLabel = Font.system(size: 10, weight: .semibold)
}

// MARK: - Motion

enum Motion {
    /// Default transition curve for the app. Deliberately a plain spring —
    /// `.snappy`/`.bouncy` are macOS 14+ only and this app has a macOS 13
    /// floor.
    static let standard: Animation = .spring(response: 0.3, dampingFraction: 0.85)
}

// MARK: - Surfaces

private struct GlassPanelModifier: ViewModifier {
    let cornerRadius: CGFloat

    func body(content: Content) -> some View {
        content
            .background(
                .ultraThinMaterial,
                in: RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
            )
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .strokeBorder(Color.glassHairline, lineWidth: 1)
            )
    }
}

extension View {
    /// Wraps content in a `.ultraThinMaterial` glass card with a hairline
    /// border — the standard surface for panels, cards, and popovers
    /// throughout the app.
    func glassPanel(cornerRadius: CGFloat = 10) -> some View {
        modifier(GlassPanelModifier(cornerRadius: cornerRadius))
    }
}

/// Slim gradient progress bar — used for disk usage and per-item clean
/// progress. Fixed 4pt tall track with a cyan→indigo gradient fill.
struct GradientBar: View {
    let fraction: Double

    private var clamped: Double {
        min(max(fraction.isFinite ? fraction : 0, 0), 1)
    }

    var body: some View {
        GeometryReader { geometry in
            ZStack(alignment: .leading) {
                Capsule()
                    .fill(Color.gradientBarTrack)
                Capsule()
                    .fill(
                        LinearGradient(
                            colors: [Color.accentCyan, Color.accentIndigo],
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                    )
                    .frame(width: geometry.size.width * clamped)
            }
        }
        .frame(height: 4)
    }
}

/// Outline amber capsule badge — "REVIEW" by default for review-only
/// (`safe: false`) targets, replacing the plain text label previously used.
/// `text` is overridable so the same outline-amber chip styling covers other
/// flags without duplicating the capsule (e.g. Projects' "DIRTY"/"UNPUSHED"
/// git-status chips).
struct ReviewBadge: View {
    var text: String = "REVIEW"

    var body: some View {
        Text(text)
            .font(.sectionLabel)
            .tracking(0.5)
            .foregroundStyle(Color.reviewAmber)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .overlay(
                Capsule()
                    .strokeBorder(Color.reviewAmber.opacity(0.6), lineWidth: 1)
            )
    }
}
