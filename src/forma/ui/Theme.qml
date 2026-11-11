pragma Singleton
import QtQuick

QtObject {
    id: theme
    property string preference: "system"
    readonly property bool dark: preference === "dark" || (preference === "system" && Qt.styleHints.colorScheme === Qt.Dark)
    readonly property string fontFamily: inter.status === FontLoader.Ready ? inter.name : Qt.application.font.family
    readonly property FontLoader inter: FontLoader { source: "fonts/InterVariable.ttf" }

    readonly property color canvas: dark ? "#11161f" : "#f4f5f7"
    readonly property color surface: dark ? "#1a222e" : "#ffffff"
    readonly property color surfaceMuted: dark ? "#151d28" : "#f7f8fa"
    readonly property color surfaceRaised: dark ? "#222d3c" : "#ffffff"
    readonly property color hover: dark ? "#253248" : "#edf1f7"
    readonly property color pressed: dark ? "#30415c" : "#e2e8f2"
    readonly property color ink: dark ? "#e9eef7" : "#19283d"
    readonly property color muted: dark ? "#abb8cd" : "#5b6a80"
    readonly property color disabledInk: dark ? "#91a0b8" : "#5b6777"
    readonly property color disabled: dark ? "#242d3a" : "#edf0f4"
    readonly property color line: dark ? "#344156" : "#dce2eb"
    readonly property color lineStrong: dark ? "#60738f" : "#8796ac"
    readonly property color accent: dark ? "#9ab9ff" : "#285bc7"
    readonly property color accentHover: dark ? "#b2caff" : "#214eae"
    readonly property color accentPressed: dark ? "#7e9ee6" : "#1b4192"
    readonly property color accentLine: dark ? "#415c84" : "#c7d6ef"
    readonly property color accentLight: dark ? "#253856" : "#edf3ff"
    readonly property color accentText: dark ? "#101e36" : "#ffffff"
    readonly property color danger: dark ? "#ffa0aa" : "#ab343f"
    readonly property color dangerFill: dark ? "#3d2630" : "#fceff0"
    readonly property color dangerLine: dark ? "#764451" : "#edc8cc"
    readonly property color warning: dark ? "#edc381" : "#8b5719"
    readonly property color warningFill: dark ? "#3d3325" : "#fff6e8"
    readonly property color warningLine: dark ? "#716044" : "#ead6b5"
    readonly property color success: dark ? "#8bd4ad" : "#236e4a"
    readonly property color successFill: dark ? "#20382e" : "#edf7f1"
    readonly property color successLine: dark ? "#405f4f" : "#c9dfd1"
    readonly property color overlay: dark ? "#a0060b14" : "#660e1c32"
    readonly property color scrollbar: dark ? "#63738d" : "#a8b5c7"
    readonly property int controlHeight: 40
    readonly property int radius: 7
}
