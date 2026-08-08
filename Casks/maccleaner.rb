cask "maccleaner" do
  version "2.4.0"
  # Placeholder — replaced at release time from the published asset.
  # See docs/RELEASING.md. A wrong sha256 makes `brew install` fail loudly,
  # which is the desired failure mode for an unpublished cask.
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"

  url "https://github.com/Fullex26/MacCleaner/releases/download/v#{version}/MacCleaner-v#{version}-macos-universal.zip",
      verified: "github.com/Fullex26/MacCleaner/"
  name "MacCleaner"
  desc "Developer storage cleanup tool for Xcode, Docker, npm, and more"
  homepage "https://github.com/Fullex26/MacCleaner"

  livecheck do
    url :url
    strategy :github_latest
  end

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
