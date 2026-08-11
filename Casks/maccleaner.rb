cask "maccleaner" do
  version "2.5.0"
  sha256 "ae7c57f9c1512b35ce110177bda28ef7e3fef470718360bb983c2a23b6e2c232"

  url "https://github.com/Fullex26/MacCleaner/releases/download/v#{version}/MacCleaner-v#{version}-macos-universal.zip",
      verified: "github.com/Fullex26/MacCleaner/"
  name "MacCleaner"
  desc "Developer storage cleanup tool for Xcode, Docker, npm, and more"
  homepage "https://github.com/Fullex26/MacCleaner"

  livecheck do
    url :url
    strategy :github_latest
  end

  # auto_updates is deliberately NOT set: Homebrew skips upgrading an
  # auto_updates cask on the assumption the app updates itself, but the
  # currently-shipping 2.5.0 build predates Sparkle and can't self-update --
  # setting auto_updates true would strand every existing cask user at
  # 2.5.0 forever (neither `brew upgrade` nor the app itself would ever move
  # them forward). Re-add it once a Sparkle-bearing build (2.6.0+) is what
  # cask users are actually running -- see docs/RELEASING.md §5.
  depends_on macos: :ventura

  app "MacCleaner.app"

  zap launchctl: [
        "com.fullex.maccleaner.clean",
        "com.fullex.maccleaner.diskwatch",
      ],
      trash:     [
        "~/Library/Application Support/MacCleaner",
        "~/Library/Caches/com.fullex.MacCleaner",
        "~/Library/HTTPStorages/com.fullex.MacCleaner",
        "~/Library/LaunchAgents/com.fullex.maccleaner.clean.plist",
        "~/Library/LaunchAgents/com.fullex.maccleaner.diskwatch.plist",
        "~/Library/Preferences/com.fullex.MacCleaner.plist",
        "~/Library/Saved Application State/com.fullex.MacCleaner.savedState",
      ]
end
