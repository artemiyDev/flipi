import {expect, it, vi} from "vitest";

import {hydrateCardMedia, releaseCardMedia} from "./media";

it("replaces protected media sources with blob URLs", async () => {
  const container = document.createElement("div");
  container.innerHTML = '<img src="/api/media/5">';
  const createObjectURL = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:image");
  const revokeObjectURL = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
  const loadMedia = vi.fn().mockResolvedValue(new Blob(["image"]));

  const urls = await hydrateCardMedia(container, [], loadMedia);

  expect(loadMedia).toHaveBeenCalledWith(5);
  expect(container.querySelector("img")?.src).toBe("blob:image");
  releaseCardMedia(container, urls);
  expect(revokeObjectURL).toHaveBeenCalledWith("blob:image");
  createObjectURL.mockRestore();
  revokeObjectURL.mockRestore();
});
