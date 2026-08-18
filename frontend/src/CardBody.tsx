import {useEffect, useRef} from "react";

import type {Media} from "./api";
import {hydrateCardMedia, releaseCardMedia} from "./media";

export function CardBody({
  questionHtml,
  answerHtml,
  cardCss,
  media,
}: {
  questionHtml: string;
  answerHtml?: string;
  cardCss: string | null;
  media: Media[];
}): JSX.Element {
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) {
      return;
    }
    const root = host.shadowRoot ?? host.attachShadow({mode: "open"});
    root.replaceChildren();
    if (cardCss !== null) {
      const style = document.createElement("style");
      style.textContent = cardCss;
      root.append(style);
    }
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `${questionHtml}${answerHtml ? `<hr>${answerHtml}` : ""}`;
    root.append(card);

    let active = true;
    let objectUrls: string[] = [];
    hydrateCardMedia(root, media).then((loadedUrls) => {
      if (active) {
        objectUrls = loadedUrls;
      } else {
        releaseCardMedia(root, loadedUrls);
      }
    });
    return () => {
      active = false;
      releaseCardMedia(root, objectUrls);
    };
  }, [answerHtml, cardCss, media, questionHtml]);

  return <div className="card-content" ref={hostRef} />;
}
