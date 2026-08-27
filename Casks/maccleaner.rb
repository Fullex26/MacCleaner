cask "maccleaner" do
  version "2.12.0"
  sha256 "c041ccbce2cc4607cf604bf031798c69c464ff03e49b75bc981496f6b5823bcc"

  url "https://github.com/Fullex26/MacCleaner/releases/download/v#{version}/MacCleaner-v#{version}-macos-universal.zip",
      verified: "github.com/Fullex26/MacCleaner/"
  name "MacCleaner"
  desc "Developer storage cleanup tool for Xcode, Docker, npm, and more"
  homepage "https://github.com/Fullex26/MacCleaner"

  livecheck do
    url :url
    strategy :github_latest
  end

  auto_updates true
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
