; 歌词提取器 安装包脚本（Inno Setup 6）
; 先运行 PyInstaller 得到 dist\LyricsExtractor\ 应用目录，再用本脚本生成 setup 安装包。

#define MyAppName "歌词提取器"
#define MyAppNameEn "LyricsExtractor"
#define MyAppVersion "0.4.0"
#define MyAppExeName "LyricsExtractor.exe"
#define MyAppPublisher "good"

[Setup]
; AppId 是本应用的唯一标识，卸载/升级都靠它；一旦发布请不要再改动
AppId={{8A1C7E52-52A1-4B62-9C3B-1F6F5B9C0A01}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppNameEn}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=歌词提取器-安装包-v{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#MyAppExeName}
ShowLanguageDialog=no

[Languages]
; 简体中文语言文件收在仓库里（官方安装包不附带），与脚本同目录
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式(&D)"; GroupDescription: "附加任务："

[Files]
Source: "..\dist\LyricsExtractor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 升级/卸载时清理应用目录内可能残留的日志等文件
Type: filesandordirs; Name: "{app}"
