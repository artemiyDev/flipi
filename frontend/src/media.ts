import {fetchMedia, type Media} from "./api";

type MediaLoader = (id: number) => Promise<Blob>;

function mediaIdFromImage(image: HTMLImageElement): number | null {
  const knownId = image.dataset.mediaId;
  if (knownId) {
    return Number(knownId);
  }
  const match = image.getAttribute("src")?.match(/^\/api\/media\/(\d+)$/);
  return match ? Number(match[1]) : null;
}

export async function hydrateCardMedia(
  container: HTMLElement | ShadowRoot,
  media: Media[],
  loadMedia: MediaLoader = fetchMedia,
): Promise<string[]> {
  const objectUrls: string[] = [];
  const images = Array.from(container.querySelectorAll<HTMLImageElement>(
    'img[src^="/api/media/"], img[data-media-id]',
  ));

  await Promise.all(images.map(async (image) => {
    const id = mediaIdFromImage(image);
    if (id === null) {
      return;
    }
    const objectUrl = URL.createObjectURL(await loadMedia(id));
    image.dataset.mediaId = String(id);
    image.src = objectUrl;
    objectUrls.push(objectUrl);
  }));

  await Promise.all(media
    .filter((item) => item.content_type?.startsWith("audio/"))
    .map(async (item) => {
      const audio = document.createElement("audio");
      const objectUrl = URL.createObjectURL(await loadMedia(item.id));
      audio.controls = true;
      audio.src = objectUrl;
      audio.dataset.mediaAudio = String(item.id);
      const card = container.querySelector<HTMLElement>(".card");
      (card ?? container).append(audio);
      objectUrls.push(objectUrl);
    }));

  return objectUrls;
}

export function releaseCardMedia(container: HTMLElement | ShadowRoot, objectUrls: string[]): void {
  for (const url of objectUrls) {
    URL.revokeObjectURL(url);
  }
  container.querySelectorAll("audio[data-media-audio]").forEach((audio) => audio.remove());
}
