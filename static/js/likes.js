import { getToken } from "/static/js/auth.js";

const HEART_SVG =
  '<svg class="like-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20.5s-7.5-4.6-9.2-9.3C1.6 7.8 3.9 4.5 7.3 4.5c2 0 3.5 1.1 4.7 2.7 1.2-1.6 2.7-2.7 4.7-2.7 3.4 0 5.7 3.3 4.5 6.7-1.7 4.7-9.2 9.3-9.2 9.3z"/></svg>';

// Same markup as the like_button macro in templates/macros.html
export function likeButtonHTML(postId, likes) {
  return `
    <button type="button" class="like-btn" data-post-id="${Number(postId)}" aria-pressed="false">
      ${HEART_SVG}
      <span class="visually-hidden">Like</span>
      <span class="like-count">${Number(likes)}</span>
    </button>
  `;
}

function setLikeState(button, liked, likes) {
  button.setAttribute("aria-pressed", liked);
  if (likes !== undefined) {
    button.querySelector(".like-count").textContent = likes;
  }
}

// Mark the buttons inside `roots` that the logged-in user has already liked
export async function syncLikedState(roots = [document]) {
  const token = getToken();
  const buttons = roots.flatMap((root) => [...root.querySelectorAll(".like-btn")]);
  if (!token || buttons.length === 0) return;

  const params = new URLSearchParams();
  for (const button of buttons) {
    params.append("post_ids", button.dataset.postId);
  }

  try {
    const response = await fetch(`/api/posts/liked?${params}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!response.ok) return;

    const liked = new Set(await response.json());
    for (const button of buttons) {
      setLikeState(button, liked.has(Number(button.dataset.postId)));
    }
  } catch (error) {
    console.error("Error loading likes:", error);
  }
}

async function toggleLike(button) {
  const token = getToken();
  if (!token) {
    window.location.href = "/login";
    return;
  }

  const wasLiked = button.getAttribute("aria-pressed") === "true";
  button.disabled = true;

  try {
    const response = await fetch(`/api/posts/${button.dataset.postId}/like`, {
      method: wasLiked ? "DELETE" : "POST",
      headers: { Authorization: `Bearer ${token}` },
    });

    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!response.ok) throw new Error(`Like request failed: ${response.status}`);

    const data = await response.json();
    setLikeState(button, data.liked, data.likes);
  } catch (error) {
    console.error("Error updating like:", error);
  } finally {
    button.disabled = false;
  }
}

// One delegated listener covers server-rendered and Load More posts alike.
// ES modules run once, so importing this file from several scripts is safe.
document.addEventListener("click", (event) => {
  const button = event.target.closest(".like-btn");
  if (button) toggleLike(button);
});

syncLikedState();
