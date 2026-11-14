/**
 * Client-side mirror of the server password policy
 * (backend utils/validation.py::validate_password_strength). The server stays
 * authoritative; this only spares a round trip and shows the same rules.
 * Returns an error message, or null when the password satisfies the policy.
 */
export function passwordPolicyError(password: string): string | null {
  // Login trims the submitted password, so a padded one could never sign in.
  if (password !== password.trim()) {
    return "Password must not start or end with a space.";
  }
  if (password.length < 8) {
    return "Password must be at least 8 characters.";
  }
  if (!/[A-Z]/.test(password) || !/[a-z]/.test(password) || !/\d/.test(password)) {
    return "Password must contain an uppercase letter, a lowercase letter and a digit.";
  }
  return null;
}
