const ACCESS_KEY = "invoiceflow.access";
const REFRESH_KEY = "invoiceflow.refresh";

export function getAccessToken() {
  return localStorage.getItem(ACCESS_KEY);
}

export function getRefreshToken() {
  return localStorage.getItem(REFRESH_KEY);
}

export function setAccessToken(access: string) {
  localStorage.setItem(ACCESS_KEY, access);
}

export function setTokens(access: string, refresh: string) {
  setAccessToken(access);
  localStorage.setItem(REFRESH_KEY, refresh);
}

export function signOut() {
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
  // A full reload also clears the previous merchant's data from memory.
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  window.location.assign("/login");
}

// Lets a signed-in page notice when another tab signs out.
export function subscribeToTokens(onChange: () => void) {
  window.addEventListener("storage", onChange);
  return () => window.removeEventListener("storage", onChange);
}
