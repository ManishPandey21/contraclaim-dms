import { describe, expect, it } from "vitest";

import { withIdempotencyKey } from "../plan-settings-api";

// Independent review 7 (R7-5, G-2): a retried change reuses its key; a new change gets a new one.
describe("withIdempotencyKey", () => {
  it("reuses the key after a failure and starts afresh after success", async () => {
    const seen: string[] = [];
    await expect(
      withIdempotencyKey("upgrade:s1:pro", async (key) => {
        seen.push(key);
        throw new Error("502");
      })
    ).rejects.toThrow("502");
    await withIdempotencyKey("upgrade:s1:pro", async (key) => {
      seen.push(key);
      return "ok";
    });
    await withIdempotencyKey("upgrade:s1:pro", async (key) => {
      seen.push(key);
      return "ok";
    });
    expect(seen[0]).toBe(seen[1]);
    expect(seen[2]).not.toBe(seen[1]);
  });

  it("gives different changes different keys", async () => {
    const keys: string[] = [];
    await withIdempotencyKey("cancel:s1:{}", async (key) => keys.push(key));
    await withIdempotencyKey("cancel:s2:{}", async (key) => keys.push(key));
    expect(keys[0]).not.toBe(keys[1]);
  });
});
