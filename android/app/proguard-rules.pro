# The page calls PaBridge.reportClick by name (click-watch.js): keep the JavaScript interface's methods.
-keepclassmembers class dev.privateagent.android.web.PaBridge {
    @android.webkit.JavascriptInterface <methods>;
}
-keepattributes JavascriptInterface
