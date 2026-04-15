// Copy to firebase.config.js and replace with your real values.
// For Vercel, load these values from environment variables.
export const firebaseConfig = {
  apiKey: 'REPLACE_WITH_FIREBASE_API_KEY',
  authDomain: 'REPLACE_WITH_FIREBASE_AUTH_DOMAIN',
  projectId: 'REPLACE_WITH_FIREBASE_PROJECT_ID',
  storageBucket: 'REPLACE_WITH_FIREBASE_STORAGE_BUCKET',
  messagingSenderId: 'REPLACE_WITH_FIREBASE_MESSAGING_SENDER_ID',
  appId: 'REPLACE_WITH_FIREBASE_APP_ID',
};

export const collectionNames = {
  users: 'users',
  subscriptions: 'subscriptions',
  apiKeys: 'apiKeys',
  usageEvents: 'usageEvents',
};
