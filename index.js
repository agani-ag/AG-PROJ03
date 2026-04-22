// Import the background task module FIRST so TaskManager.defineTask() is
// executed before React boots — this is required for Android headless JS
// to find and run the task when the app is killed.
import './utils/backgroundAuditTask';

import { registerRootComponent } from 'expo';
import App from './App';

registerRootComponent(App);
