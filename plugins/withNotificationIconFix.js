const { withAndroidManifest } = require('@expo/config-plugins');

/**
 * Custom Expo config plugin to add tools:replace="android:resource" 
 * to Firebase notification meta-data entries that conflict with
 * @react-native-firebase/messaging's own manifest entries.
 */
module.exports = function withNotificationIconFix(config) {
  return withAndroidManifest(config, async (config) => {
    const manifest = config.modResults;

    // Ensure tools namespace is declared
    manifest.manifest.$ = {
      ...manifest.manifest.$,
      'xmlns:tools': 'http://schemas.android.com/tools',
    };

    const application = manifest.manifest.application?.[0];
    if (!application || !application['meta-data']) return config;

    const conflictingKeys = [
      'com.google.firebase.messaging.default_notification_color',
      'com.google.firebase.messaging.default_notification_icon',
    ];

    for (const metaData of application['meta-data']) {
      const name = metaData.$?.['android:name'];
      if (conflictingKeys.includes(name)) {
        metaData.$['tools:replace'] = 'android:resource';
      }
    }

    return config;
  });
};
