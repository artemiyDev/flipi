import {render, waitFor} from "@testing-library/react";
import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";

import {CardBody} from "./CardBody";

const apiMocks = vi.hoisted(() => ({
  fetchMedia: vi.fn(),
}));

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  fetchMedia: apiMocks.fetchMedia,
}));

describe("CardBody", () => {
  afterEach(() => vi.useRealTimers());

  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.fetchMedia.mockResolvedValue(new Blob(["image"]));
  });

  it("renders model CSS and card content inside an open shadow root", async () => {
    const {container} = render(
      <CardBody
        questionHtml="<b>Question</b>"
        answerHtml="<i>Answer</i>"
        cardCss=".card { color: red; }"
        media={[]}
      />,
    );

    await waitFor(() => expect(container.querySelector(".card-content")?.shadowRoot).not.toBeNull());
    const root = container.querySelector(".card-content")?.shadowRoot;

    expect(root?.querySelector("style")?.textContent).toBe(".card { color: red; }");
    expect(root?.querySelector(".card")?.innerHTML).toContain("<b>Question</b>");
    expect(root?.querySelector(".card")?.innerHTML).toContain("<i>Answer</i>");
  });

  it("keeps model CSS out of elements outside the shadow root", async () => {
    const outside = document.createElement("div");
    outside.className = "card";
    document.body.append(outside);
    const {container} = render(
      <CardBody questionHtml="Question" cardCss=".card { color: red; }" media={[]} />,
    );

    await waitFor(() => expect(container.querySelector(".card-content")?.shadowRoot?.querySelector("style")).not.toBeNull());

    expect(outside.querySelector("style")).toBeNull();
    outside.remove();
  });

  it("hydrates image media inside the shadow root", async () => {
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:media-5");
    const {container} = render(
      <CardBody
        questionHtml='<img src="/api/media/5">'
        cardCss={null}
        media={[{id: 5, name: "image.png", content_type: "image/png"}]}
      />,
    );

    const root = container.querySelector(".card-content")?.shadowRoot;
    await waitFor(() => expect(root?.querySelector("img")?.getAttribute("src")).toBe("blob:media-5"));
    expect(root?.querySelector("style")).toBeNull();
    expect(apiMocks.fetchMedia).toHaveBeenCalledWith(5);
  });
});
