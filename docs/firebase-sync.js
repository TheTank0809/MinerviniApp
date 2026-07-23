/* Cross-device sync for Shortlist/Buy marks via Firebase Realtime Database.
   Anonymous auth only — a low-friction deterrent matching the passphrase gate's own
   documented security bar, not real security. Revisit (real sign-in, UID-scoped rules)
   if this database ever holds anything more sensitive than personal watchlist tags.
   Fails silently and leaves the app on localStorage-only marks if offline/blocked. */
import { initializeApp } from "https://www.gstatic.com/firebasejs/12.16.0/firebase-app.js";
import { getAuth, signInAnonymously } from "https://www.gstatic.com/firebasejs/12.16.0/firebase-auth.js";
import { getDatabase, ref, onValue, set, remove } from "https://www.gstatic.com/firebasejs/12.16.0/firebase-database.js";

var firebaseConfig = {
  apiKey: "AIzaSyAYX7OMzxbe0fXEc0m8I6a5AxVcMIMoiPg",
  authDomain: "stocks-tracker-2b489.firebaseapp.com",
  databaseURL: "https://stocks-tracker-2b489-default-rtdb.asia-southeast1.firebasedatabase.app",
  projectId: "stocks-tracker-2b489",
  storageBucket: "stocks-tracker-2b489.firebasestorage.app",
  messagingSenderId: "193777846444",
  appId: "1:193777846444:web:f0642b27ade8f202783709"
};

var app = initializeApp(firebaseConfig);
var auth = getAuth(app);
var db = getDatabase(app);

var ready = signInAnonymously(auth).catch(function (err) {
  console.warn("Minervini sync: anonymous sign-in failed, marks stay local-only this session.", err);
  return Promise.reject(err);
});

function subscribe(kind, cb) {
  ready.then(function () {
    onValue(ref(db, "marks/" + kind), function (snap) { cb(snap.val() || {}); });
  }).catch(function () {});
}
function toggle(kind, ticker, present) {
  ready.then(function () {
    var r = ref(db, "marks/" + kind + "/" + ticker);
    return present ? set(r, true) : remove(r);
  }).catch(function () {});
}
function replace(kind, obj) {
  ready.then(function () { return set(ref(db, "marks/" + kind), obj); }).catch(function () {});
}

window.mvSync = { subscribe: subscribe, toggle: toggle, replace: replace };
// app.js's classic <script> always runs before this module (modules are deferred to
// after parsing), so window.mvSyncInit is guaranteed to already be defined here.
if (window.mvSyncInit) window.mvSyncInit();
