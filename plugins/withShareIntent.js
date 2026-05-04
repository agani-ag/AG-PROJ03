const { withAndroidManifest } = require('@expo/config-plugins');

/**
 * Config plugin: Adds intent-filters to MainActivity so the app appears
 * in Android's Share sheet for ALL file types (single + multiple).
 */
function withShareIntent(config) {
  return withAndroidManifest(config, (config) => {
    const manifest = config.modResults;
    const application = manifest.manifest.application[0];
    const mainActivity = application.activity.find(
      (a) => a.$['android:name'] === '.MainActivity'
    );

    if (!mainActivity) return config;

    // Ensure intent-filter array exists
    if (!mainActivity['intent-filter']) {
      mainActivity['intent-filter'] = [];
    }

    // Share single file (any type)
    mainActivity['intent-filter'].push({
      action: [{ $: { 'android:name': 'android.intent.action.SEND' } }],
      category: [{ $: { 'android:name': 'android.intent.category.DEFAULT' } }],
      data: [{ $: { 'android:mimeType': '*/*' } }],
    });

    // Share multiple files (any type)
    mainActivity['intent-filter'].push({
      action: [{ $: { 'android:name': 'android.intent.action.SEND_MULTIPLE' } }],
      category: [{ $: { 'android:name': 'android.intent.category.DEFAULT' } }],
      data: [{ $: { 'android:mimeType': '*/*' } }],
    });

    return config;
  });
}

module.exports = withShareIntent;
