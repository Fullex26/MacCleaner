cask "maccleaner" do
  version "2.7.0"
  sha256 "87f21180405671b60e7044f2b7583c930e967d9efe89d07fb02c3a56871023c1"

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
