  import React, {
  useState,
  useEffect,
  useRef,
  useCallback,
  KeyboardEvent,
  FormEvent,
} from 'react';

// ============================================================================
// 1. TYPES (maps to src/types.ts)
// ============================================================================

export type SeriesType = 'manga' | 'manhwa' | 'manhua';
export type SeriesTypeFilter = 'all' | SeriesType;
export type SeriesFormat = 'oneshot' | 'short_series' | 'series' | 'unknown';
export type SeriesFormatFilter = 'any' | 'oneshot' | 'short_series' | 'series';
export type ContentRating = 'safe' | 'suggestive' | 'erotica';

export interface Filters {
  type: SeriesTypeFilter;
  include_suggestive: boolean;
  include_mature: boolean;
  readable_only: boolean;
  format: SeriesFormatFilter;
}

export interface SeriesItem {
  id: number;
  title: string;
  type: SeriesType;
  year: number | null;
  status: string | null;
  rating: number | null;
  cover: string | null;
  synopsis: string;
  tags: string[];
  matched_tags: string[];
  content_rating: ContentRating;
  readable: boolean;
  format: SeriesFormat;
}

export interface IntentSummary {
  must: string[];
  boosts: string[];
  keywords: string[];
  similar_to: string[];
  format: SeriesFormatFilter | null;
  readable_only: boolean;
}

export interface RecommendResponse {
  intent_id: string;
  intent: IntentSummary;
  results: SeriesItem[];
  total: number;
  latency_ms: {
    llm: number;
    db: number;
  };
  degraded: boolean;
  not_found_titles: string[];
  skipped_titles: string[];
}

export interface RecommendRequest {
  text: string;
  filters: Filters;
  seed_titles?: string[];
}

export interface RefilterRequest {
  intent_id: string;
  filters: Filters;
  offset?: number;
}

export interface FeedbackRequest {
  intent_id: string;
  series_id: number;
  vote: 1 | -1;
  note?: string;
}

export interface ApiErrorPayload {
  error: {
    code: string;
    message: string;
  };
}

export type AppErrorKind = 'rate_limit' | 'network' | 'server';

export class ApiClientError extends Error {
  public readonly status: number;
  public readonly code: string;
  public readonly kind: AppErrorKind;

  constructor(message: string, status: number, code: string, kind: AppErrorKind) {
    super(message);
    this.name = 'ApiClientError';
    this.status = status;
    this.code = code;
    this.kind = kind;
  }
}

// ============================================================================
// 2. ENVIRONMENT & MOCK FIXTURES (maps to src/fixtures.ts)
// ============================================================================

const readEnv = (key: string): string | undefined => {
  try {
    const meta = import.meta as unknown as { env?: Record<string, string | undefined> };
    return meta?.env?.[key];
  } catch {
    return undefined;
  }
};

const API_BASE_URL = (readEnv('VITE_API_URL') || '').replace(/\/+$/, '');
// Mock mode is strictly opt-in via VITE_MOCK=1
const IS_MOCK_MODE = readEnv('VITE_MOCK') === '1';

const AGE_STORAGE_KEY = 'koma_age_confirmed_18';
const THEME_STORAGE_KEY = 'koma_theme';

const DEFAULT_FILTERS: Filters = {
  type: 'all',
  include_suggestive: false,
  include_mature: false,
  readable_only: false,
  format: 'any',
};

const EXAMPLE_PLACEHOLDERS: [string, string] = [
  'regression revenge story of a betrayed tycoon',
  "like Omniscient Reader's Viewpoint but with a female lead",
];

const HERO_POSTER_WALL: string[] = [
  '/posters/01_solo_leveling.jpg',
  '/posters/02_spirit_fingers.jpg',
  '/posters/03_one_piece.jpg',
  '/posters/04_uma_musume_cinderella_gray.jpg',
  '/posters/05_omniscient_reader.jpg',
  '/posters/06_whale_star.jpg',
  '/posters/07_attack_on_titan.jpg',
  '/posters/08_kaiji_one_poker.jpg',
  '/posters/09_greatest_estate_developer.jpg',
  '/posters/10_crown_princess_scandal.jpg',
];

type AppPage = 'home' | 'faqs';
type FaqCategoryId = 'all' | 'search' | 'filters' | 'safety' | 'reading';

interface FaqItem {
  id: string;
  category: Exclude<FaqCategoryId, 'all'>;
  question: string;
  answer: string;
}

const FAQ_CATEGORIES: { id: FaqCategoryId; label: string }[] = [
  { id: 'all', label: 'All topics' },
  { id: 'search', label: 'Search & AI' },
  { id: 'filters', label: 'Filters & Formats' },
  { id: 'safety', label: 'Safety & 18+' },
  { id: 'reading', label: 'Reading & Feedback' },
];

const FAQ_ITEMS: FaqItem[] = [
  {
    id: 'how-search-works',
    category: 'search',
    question: 'How does Koma understand free-text requests?',
    answer:
      'When you submit a prompt, the backend extracts structured tropes ("must" tags, "boosts", and keywords) and matches them against our manga, manhwa, and manhua catalog. The chips above your results show exactly how your request was interpreted.',
  },
  {
    id: 'seed-titles',
    category: 'search',
    question: 'What does the "I liked..." option do?',
    answer:
      'You can add up to 3 titles you already enjoyed to anchor the recommendation tone, pacing, and tropes. If a title is not found in the catalog, Koma lets you know in a banner and continues ranking with the rest of your prompt.',
  },
  {
    id: 'result-count',
    category: 'search',
    question: 'How many titles does Koma return per search?',
    answer:
      'Each search or filter update returns up to 13 curated recommendations ranked for relevance and community score so you can scan strong matches without getting overwhelmed.',
  },
  {
    id: 'degraded-mode',
    category: 'search',
    question: 'What happens if AI semantic matching is temporarily busy?',
    answer:
      'If the language-model router hits rate limits or upstream errors, Koma automatically falls back to keyword-based matching and displays a status notice so you still get relevant titles.',
  },
  {
    id: 'instant-refilter',
    category: 'filters',
    question: 'Why are filter changes faster than the first search?',
    answer:
      'Your initial search stores an intent ID on the backend. Switching between Manhwa, Manga, Manhua, Format, Official English, or content toggles calls the lightweight /refilter endpoint directly against the database without re-running AI.',
  },
  {
    id: 'origin-types',
    category: 'filters',
    question: 'What is the difference between Manga, Manhwa, and Manhua?',
    answer:
      'Manga comes from Japan (traditionally black-and-white page layouts), Manhwa comes from South Korea (often full-color vertical webtoons), and Manhua comes from China, Taiwan, or Hong Kong (commonly full-color long-strip comics).',
  },
  {
    id: 'format-options',
    category: 'filters',
    question: 'What do the Format filter options mean?',
    answer:
      'You can narrow results to One-shot (single self-contained stories), Short series (compact multi-chapter runs), or Full series (longer ongoing or completed runs), or keep Any format selected.',
  },
  {
    id: 'official-english',
    category: 'filters',
    question: 'What does "Official English only" mean?',
    answer:
      'Enabling this filter limits results to catalog entries marked as having an officially readable licensed English release.',
  },
  {
    id: 'mature-content',
    category: 'safety',
    question: 'How is 18+ and suggestive content handled?',
    answer:
      'Adult (18+) and suggestive content are off by default. Turning on 18+ content requires confirming you are 18 or older (remembered in browser localStorage), and covers on suggestive or 18+ cards stay blurred behind a "Tap to reveal" overlay until you choose to view them.',
  },
  {
    id: 'disable-mature',
    category: 'safety',
    question: 'Can I turn off suggestive or 18+ filters at any time?',
    answer:
      'Yes. Switching off "Include suggestive" or "18+ content" in the filter bar immediately re-filters your active recommendations and removes those titles from view.',
  },
  {
    id: 'click-to-read',
    category: 'reading',
    question: 'What happens when I click a recommendation card or title?',
    answer:
      'Clicking any unblurred card (or its title) opens a Google search in a new tab with the series title and "read" attached so you can quickly locate where to read it.',
  },
  {
    id: 'feedback-controls',
    category: 'reading',
    question: 'How do thumbs up, thumbs down, and comments work?',
    answer:
      'You can cast one thumbs up or thumbs down vote per card in a session—and optionally attach a one-line comment—which is sent to the /feedback endpoint to record match quality.',
  },
];

const MOCK_CATALOG: SeriesItem[] = [
  {
    id: 1,
    title: 'Reborn Rich',
    type: 'manhwa',
    year: 2022,
    status: 'Ongoing',
    rating: 8.6,
    cover:
      'https://images.unsplash.com/photo-1486406146926-c627a92ad1ab?auto=format&fit=crop&w=480&q=80',
    synopsis:
      'After ten years of loyal service to the Soonyang chaebol empire, Yoon Hyun-woo is framed for embezzlement and assassinated—only to wake up in 1987 as the conglomerate’s youngest grandson with full knowledge of the future.',
    tags: ['regression', 'revenge', 'business', 'chaebol', 'smart mc', 'drama'],
    matched_tags: ['regression', 'revenge', 'tycoon'],
    content_rating: 'safe',
    readable: true,
    format: 'series',
  },
  {
    id: 2,
    title: "Omniscient Reader's Viewpoint",
    type: 'manhwa',
    year: 2020,
    status: 'Ongoing',
    rating: 9.1,
    cover:
      'https://images.unsplash.com/photo-1519681393784-d120267933ba?auto=format&fit=crop&w=480&q=80',
    synopsis:
      'Kim Dokja is the sole reader of an obscure apocalyptic web novel. The moment the final chapter publishes, fiction becomes reality—and his knowledge is the only map to surviving the scenarios.',
    tags: ['apocalypse', 'system', 'constellations', 'meta', 'survival', 'smart mc'],
    matched_tags: ['meta', 'apocalypse', 'system'],
    content_rating: 'safe',
    readable: true,
    format: 'series',
  },
  {
    id: 3,
    title: 'I Stole the First Ranker’s Soul',
    type: 'manhwa',
    year: 2022,
    status: 'Completed',
    rating: 8.4,
    cover:
      'https://images.unsplash.com/photo-1534447677768-be436bb09401?auto=format&fit=crop&w=480&q=80',
    synopsis:
      'An ordinary office worker awakens as a gathering-type hunter inside lethal dungeons. After accidentally binding the ghost of Korea’s #1 hunter to herself, she climbs the tower on her own terms.',
    tags: ['female lead', 'dungeons', 'system', 'hunters', 'comedy', 'action'],
    matched_tags: ['female lead', 'system', 'dungeons'],
    content_rating: 'safe',
    readable: true,
    format: 'series',
  },
  {
    id: 4,
    title: 'Real Man (A Man’s Man)',
    type: 'manhwa',
    year: 2020,
    status: 'Ongoing',
    rating: 8.9,
    cover:
      'https://images.unsplash.com/photo-1507679799987-c73779587ccf?auto=format&fit=crop&w=480&q=80',
    synopsis:
      'Han Yoo-hyun rose to CEO of Hansung Electronics by sacrificing everyone he loved. Given a second chance as a rookie intern twenty years earlier, he dismantles corporate corruption from the inside.',
    tags: ['regression', 'corporate', 'strategy', 'drama', 'smart mc', 'slice of life'],
    matched_tags: ['regression', 'tycoon', 'corporate'],
    content_rating: 'safe',
    readable: true,
    format: 'series',
  },
  {
    id: 5,
    title: 'The Perks of Being an S-Class Heroine',
    type: 'manhwa',
    year: 2022,
    status: 'Ongoing',
    rating: 8.5,
    cover:
      'https://images.unsplash.com/photo-1518709268805-4e9042af9f23?auto=format&fit=crop&w=480&q=80',
    synopsis:
      'Reincarnated into a brutal transmigration-insurance tower world, Ailette purchases an S-class survival package and manipulates constellation sponsors to rewrite a doomed storyline.',
    tags: ['female lead', 'constellations', 'system', 'fantasy', 'transmigration'],
    matched_tags: ['female lead', 'constellations', 'system'],
    content_rating: 'safe',
    readable: true,
    format: 'series',
  },
  {
    id: 6,
    title: 'Look Back',
    type: 'manga',
    year: 2021,
    status: 'Completed',
    rating: 8.8,
    cover:
      'https://images.unsplash.com/photo-1513364776144-60967b0f800f?auto=format&fit=crop&w=480&q=80',
    synopsis:
      'Overly confident fourth-grader Fujino and shut-in prodigy Kyomoto bond over a shared obsession with drawing manga—an unforgettable one-shot portrait of rivalry, devotion, and grief.',
    tags: ['drama', 'slice of life', 'art', 'emotional', 'award-winning'],
    matched_tags: ['emotional', 'drama'],
    content_rating: 'safe',
    readable: true,
    format: 'oneshot',
  },
  {
    id: 7,
    title: 'The Horizon',
    type: 'manhwa',
    year: 2016,
    status: 'Completed',
    rating: 8.9,
    cover:
      'https://images.unsplash.com/photo-1478760329108-5c3ed9d495a0?auto=format&fit=crop&w=480&q=80',
    synopsis:
      'In the quiet aftermath of an unnamed war, a nameless boy and girl walk down an endless highway searching for a place where violence cannot reach them.',
    tags: ['post-apocalyptic', 'psychological', 'tragedy', 'short', 'philosophical'],
    matched_tags: ['apocalypse', 'psychological'],
    content_rating: 'safe',
    readable: true,
    format: 'short_series',
  },
  {
    id: 8,
    title: 'Lord of the Mysteries',
    type: 'manhua',
    year: 2020,
    status: 'Ongoing',
    rating: 8.3,
    cover:
      'https://images.unsplash.com/photo-1509198397868-475647b2a1e5?auto=format&fit=crop&w=480&q=80',
    synopsis:
      'Awakening in a Victorian steampunk world of eldritch potions and secret societies, Zhou Mingrui assumes the identity of Klein Moretti and builds a hidden tarot council from the fog.',
    tags: ['mystery', 'steampunk', 'smart mc', 'occult', 'worldbuilding'],
    matched_tags: ['smart mc', 'mystery', 'worldbuilding'],
    content_rating: 'safe',
    readable: false,
    format: 'series',
  },
  {
    id: 9,
    title: 'Chainsaw Man',
    type: 'manga',
    year: 2018,
    status: 'Ongoing',
    rating: 8.7,
    cover:
      'https://images.unsplash.com/photo-1550684848-fac1c5b4e853?auto=format&fit=crop&w=480&q=80',
    synopsis:
      'Buried in yakuza debt, Denji fuses with his devil dog Pochita and is drafted into Public Safety’s devil hunters—where survival is chaotic, darkly funny, and razor-sharp.',
    tags: ['action', 'dark fantasy', 'demons', 'gore', 'unhinged'],
    matched_tags: ['dark fantasy', 'action'],
    content_rating: 'suggestive',
    readable: true,
    format: 'series',
  },
  {
    id: 10,
    title: 'The Crimson Ledger',
    type: 'manhwa',
    year: 2023,
    status: 'Ongoing',
    rating: 8.1,
    cover:
      'https://images.unsplash.com/photo-1541701494587-cb58502866ab?auto=format&fit=crop&w=480&q=80',
    synopsis:
      'Betrayed by the syndicate heiress who bankrolled his rise, a disgraced fixer returns under a stolen identity to burn down the high-society auction house piece by piece.',
    tags: ['revenge', 'noir', 'romance', 'psychological', 'high society'],
    matched_tags: ['revenge', 'noir', 'betrayal'],
    content_rating: 'suggestive',
    readable: false,
    format: 'series',
  },
  {
    id: 11,
    title: 'The Broken Ring: This Marriage Will Fail Anyway',
    type: 'manhwa',
    year: 2022,
    status: 'Ongoing',
    rating: 8.5,
    cover:
      'https://images.unsplash.com/photo-1490750967868-88aa4486c946?auto=format&fit=crop&w=480&q=80',
    synopsis:
      'After two ruinous lifetimes, Inés arranges a calculated marriage to a notoriously reckless nobleman—expecting a quiet divorce, only to uncover truths buried across every regression.',
    tags: ['regression', 'female lead', 'drama', 'romance', 'nobility'],
    matched_tags: ['regression', 'female lead', 'drama'],
    content_rating: 'erotica',
    readable: true,
    format: 'series',
  },
  {
    id: 12,
    title: 'Goodbye, Eri',
    type: 'manga',
    year: 2022,
    status: 'Completed',
    rating: 8.7,
    cover:
      'https://images.unsplash.com/photo-1507608616759-54f48f0af0ee?auto=format&fit=crop&w=480&q=80',
    synopsis:
      'A boy records his dying mother on his smartphone to create a film, blurring the boundary between cinema, memory, and reality after meeting an enigmatic girl named Eri.',
    tags: ['meta', 'psychological', 'drama', 'cinema'],
    matched_tags: ['meta', 'psychological'],
    content_rating: 'safe',
    readable: true,
    format: 'oneshot',
  },
  {
    id: 13,
    title: 'Song of the Sky Pacers',
    type: 'manhua',
    year: 2018,
    status: 'Ongoing',
    rating: 8.4,
    cover:
      'https://images.unsplash.com/photo-1506744038136-46273834b3fb?auto=format&fit=crop&w=480&q=80',
    synopsis:
      'Separated at birth, twin siblings walk opposing paths of martial arts and imperial intrigue across a lushly painted ancient frontier.',
    tags: ['martial arts', 'female lead', 'historical', 'adventure'],
    matched_tags: ['female lead', 'historical'],
    content_rating: 'safe',
    readable: false,
    format: 'series',
  },
  {
    id: 14,
    title: 'Nocturne of the Iron Crown',
    type: 'manga',
    year: 2024,
    status: 'Completed',
    rating: 7.9,
    cover:
      'https://images.unsplash.com/photo-1508739773434-c26b3d09e071?auto=format&fit=crop&w=480&q=80',
    synopsis:
      'A tightly wound 18-chapter dark thriller following a royal archivist who discovers the king’s succession ritual demands a blood debt from every noble house.',
    tags: ['dark fantasy', 'revenge', 'mystery', 'political'],
    matched_tags: ['revenge', 'dark fantasy'],
    content_rating: 'erotica',
    readable: false,
    format: 'short_series',
  },
];

// In-memory mock intent store so /refilter behaves identically to the real backend
const mockIntentStore = new Map<
  string,
  {
    text: string;
    seed_titles: string[];
    intent: IntentSummary;
    rankedIds: number[];
    degraded: boolean;
    not_found_titles: string[];
    skipped_titles: string[];
  }
>();

function applyFiltersToCatalog(items: SeriesItem[], filters: Filters): SeriesItem[] {
  return items.filter((item) => {
    if (filters.type !== 'all' && item.type !== filters.type) return false;
    if (filters.readable_only && !item.readable) return false;
    if (filters.format !== 'any' && item.format !== filters.format) return false;
    if (item.content_rating === 'erotica' && !filters.include_mature) return false;
    if (item.content_rating === 'suggestive' && !filters.include_suggestive) return false;
    return true;
  });
}

function buildMockIntent(text: string, seedTitles: string[], filters: Filters): IntentSummary {
  const lower = text.toLowerCase();
  const must: string[] = [];
  const boosts: string[] = [];

  if (lower.includes('regress') || lower.includes('time') || lower.includes('reborn')) {
    must.push('regression');
  }
  if (lower.includes('revenge') || lower.includes('betray')) {
    must.push('revenge');
  }
  if (lower.includes('female') || lower.includes('heroine') || lower.includes('fmc')) {
    must.push('female lead');
  }
  if (lower.includes('tycoon') || lower.includes('ceo') || lower.includes('business')) {
    boosts.push('corporate strategy');
  }
  if (lower.includes('omniscient') || lower.includes('system') || lower.includes('tower')) {
    must.push('system');
    boosts.push('constellations');
  }
  if (lower.includes('dark') || lower.includes('thriller')) {
    boosts.push('psychological');
  }
  if (must.length === 0) must.push('strong plot', 'character-driven');
  if (boosts.length === 0) boosts.push('smart mc', 'high stakes', 'tight pacing');

  const keywords = lower
    .split(/[^a-z0-9]+/)
    .filter((w) => w.length > 3)
    .slice(0, 6);

  return {
    must: Array.from(new Set(must)).slice(0, 4),
    boosts: Array.from(new Set(boosts)).slice(0, 4),
    keywords,
    similar_to: seedTitles,
    format: filters.format,
    readable_only: filters.readable_only,
  };
}

function waitWithAbort(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Aborted', 'AbortError'));
      return;
    }
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        reject(new DOMException('Aborted', 'AbortError'));
      },
      { once: true }
    );
  });
}

// ============================================================================
// 3. TYPED API CLIENT (maps to src/api.ts)
// ============================================================================

async function parseErrorResponse(res: Response): Promise<ApiClientError> {
  let code = `HTTP_${res.status}`;
  let message = 'Something went wrong while contacting the server.';
  try {
    const body = (await res.json()) as Partial<ApiErrorPayload>;
    if (body?.error?.code) code = body.error.code;
    if (body?.error?.message) message = body.error.message;
  } catch {
    // Response was not JSON
  }

  if (res.status === 429) {
    return new ApiClientError(
      message || 'You have sent too many searches in a short time. Please wait a moment and try again.',
      429,
      code,
      'rate_limit'
    );
  }
  return new ApiClientError(message, res.status, code, 'server');
}

async function postJson<TReq, TRes>(
  path: string,
  payload: TReq,
  signal?: AbortSignal
): Promise<TRes> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw err;
    }
    throw new ApiClientError(
      'Could not reach the recommendation server. Check your connection and try again.',
      0,
      'NETWORK_ERROR',
      'network'
    );
  }

  if (!res.ok) {
    throw await parseErrorResponse(res);
  }
  return (await res.json()) as TRes;
}

export async function apiRecommend(
  req: RecommendRequest,
  signal?: AbortSignal
): Promise<RecommendResponse> {
  if (!IS_MOCK_MODE) {
    return postJson<RecommendRequest, RecommendResponse>('/recommend', req, signal);
  }

  // Mock mode simulates realistic 1.2s LLM + DB latency
  await waitWithAbort(1150, signal);

  const lower = req.text.toLowerCase();
  // Helpful mock triggers for testing edge states in development
  if (lower.includes('429') || lower.includes('rate limit')) {
    throw new ApiClientError(
      'Too many requests right now. Please wait a few seconds and retry.',
      429,
      'RATE_LIMITED',
      'rate_limit'
    );
  }
  if (lower.includes('network error') || lower.includes('offline')) {
    throw new ApiClientError(
      'Could not reach the recommendation server. Check your connection and try again.',
      0,
      'NETWORK_ERROR',
      'network'
    );
  }

  const seedTitles = req.seed_titles || [];
  const notFound = seedTitles.filter(
    (t) => t.toLowerCase().includes('unknown') || t.toLowerCase().includes('xyz')
  );
  const degraded = lower.includes('degraded');
  const intent = buildMockIntent(req.text, seedTitles, req.filters);

  // Rank mock catalog by simple keyword/tag overlap
  const tokens = lower.split(/[^a-z0-9]+/).filter((w) => w.length > 2);
  const scored = MOCK_CATALOG.map((item) => {
    const hay = `${item.title} ${item.synopsis} ${item.tags.join(' ')}`.toLowerCase();
    let score = item.rating || 7.5;
    for (const tok of tokens) {
      if (hay.includes(tok)) score += 2.5;
    }
    return { item, score };
  }).sort((a, b) => b.score - a.score);

  const rankedItems = lower.includes('empty') ? [] : scored.map((s) => s.item);
  const intentId = `mock_${Date.now().toString(36)}`;

  mockIntentStore.set(intentId, {
    text: req.text,
    seed_titles: seedTitles,
    intent,
    rankedIds: rankedItems.map((i) => i.id),
    degraded,
    not_found_titles: notFound,
    skipped_titles: [],
  });

  const filtered = applyFiltersToCatalog(rankedItems, req.filters).slice(0, 13);

  return {
    intent_id: intentId,
    intent,
    results: filtered,
    total: filtered.length,
    latency_ms: { llm: degraded ? 0 : 1090, db: 42 },
    degraded,
    not_found_titles: notFound,
    skipped_titles: [],
  };
}

export async function apiRefilter(
  req: RefilterRequest,
  signal?: AbortSignal
): Promise<RecommendResponse> {
  if (!IS_MOCK_MODE) {
    return postJson<RefilterRequest, RecommendResponse>('/refilter', req, signal);
  }

  // Fast DB-only refilter simulation (no AI call)
  await waitWithAbort(140, signal);

  const stored = mockIntentStore.get(req.intent_id);
  const baseCatalog = stored
    ? stored.rankedIds
        .map((id) => MOCK_CATALOG.find((c) => c.id === id))
        .filter((c): c is SeriesItem => Boolean(c))
    : MOCK_CATALOG;

  const filtered = applyFiltersToCatalog(baseCatalog, req.filters).slice(0, 13);

  return {
    intent_id: req.intent_id,
    intent: stored
      ? {
          ...stored.intent,
          format: req.filters.format,
          readable_only: req.filters.readable_only,
        }
      : {
          must: ['strong plot'],
          boosts: ['smart mc'],
          keywords: [],
          similar_to: [],
          format: req.filters.format,
          readable_only: req.filters.readable_only,
        },
    results: filtered,
    total: filtered.length,
    latency_ms: { llm: 0, db: 28 },
    degraded: stored?.degraded ?? false,
    not_found_titles: stored?.not_found_titles ?? [],
    skipped_titles: stored?.skipped_titles ?? [],
  };
}

export async function apiSendFeedback(
  req: FeedbackRequest,
  signal?: AbortSignal
): Promise<{ ok: boolean }> {
  if (!IS_MOCK_MODE) {
    return postJson<FeedbackRequest, { ok: boolean }>('/feedback', req, signal);
  }
  await waitWithAbort(160, signal);
  return { ok: true };
}

// ============================================================================
// 4. ENDPOINT HOOKS — ONE HOOK PER ENDPOINT (maps to src/hooks/*.ts)
// ============================================================================

function useRecommend() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ApiClientError | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const run = useCallback(async (req: RecommendRequest): Promise<RecommendResponse | null> => {
    // Cancel any in-flight recommendation request
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    setError(null);
    try {
      const data = await apiRecommend(req, controller.signal);
      if (!controller.signal.aborted) {
        setLoading(false);
      }
      return data;
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        return null;
      }
      const apiErr =
        err instanceof ApiClientError
          ? err
          : new ApiClientError('Unexpected error while searching.', 0, 'UNKNOWN', 'server');
      setError(apiErr);
      setLoading(false);
      return null;
    }
  }, []);

  const clearError = useCallback(() => setError(null), []);

  useEffect(() => () => abortRef.current?.abort(), []);

  return { run, loading, error, clearError };
}

function useRefilter() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ApiClientError | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const run = useCallback(async (req: RefilterRequest): Promise<RecommendResponse | null> => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    setError(null);
    try {
      const data = await apiRefilter(req, controller.signal);
      if (!controller.signal.aborted) {
        setLoading(false);
      }
      return data;
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        return null;
      }
      const apiErr =
        err instanceof ApiClientError
          ? err
          : new ApiClientError('Failed to update filters.', 0, 'UNKNOWN', 'server');
      setError(apiErr);
      setLoading(false);
      return null;
    }
  }, []);

  const clearError = useCallback(() => setError(null), []);

  useEffect(() => () => abortRef.current?.abort(), []);

  return { run, loading, error, clearError };
}

function useFeedback() {
  // One vote per card per session: map of series_id -> 1 | -1
  const [votes, setVotes] = useState<Record<number, 1 | -1>>({});
  const [notesSaved, setNotesSaved] = useState<Record<number, string>>({});
  const [pendingIds, setPendingIds] = useState<Record<number, boolean>>({});

  const submitVote = useCallback(
    async (intentId: string, seriesId: number, vote: 1 | -1, note?: string) => {
      // Enforce one vote per card per session
      if (votes[seriesId] !== undefined || pendingIds[seriesId]) return;

      // Optimistic UI update
      setVotes((prev) => ({ ...prev, [seriesId]: vote }));
      setPendingIds((prev) => ({ ...prev, [seriesId]: true }));

      try {
        await apiSendFeedback({
          intent_id: intentId,
          series_id: seriesId,
          vote,
          ...(note?.trim() ? { note: note.trim() } : {}),
        });
        if (note?.trim()) {
          setNotesSaved((prev) => ({ ...prev, [seriesId]: note.trim() }));
        }
      } catch {
        // Revert optimistic vote if network fails
        setVotes((prev) => {
          const next = { ...prev };
          delete next[seriesId];
          return next;
        });
      } finally {
        setPendingIds((prev) => {
          const next = { ...prev };
          delete next[seriesId];
          return next;
        });
      }
    },
    [votes, pendingIds]
  );

  const submitNote = useCallback(
    async (intentId: string, seriesId: number, note: string) => {
      const cleaned = note.trim();
      const existingVote = votes[seriesId];
      if (!cleaned || existingVote === undefined || notesSaved[seriesId]) return;

      // Optimistically save the optional one-line note
      setNotesSaved((prev) => ({ ...prev, [seriesId]: cleaned }));
      try {
        await apiSendFeedback({
          intent_id: intentId,
          series_id: seriesId,
          vote: existingVote,
          note: cleaned,
        });
      } catch {
        setNotesSaved((prev) => {
          const next = { ...prev };
          delete next[seriesId];
          return next;
        });
      }
    },
    [votes, notesSaved]
  );

  return { votes, notesSaved, submitVote, submitNote };
}

// ============================================================================
// 5. CRISP INLINE ICONS (zero external UI dependencies — src/components/Icons.tsx)
// ============================================================================

const IconSearch = ({ className = 'w-4 h-4' }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
    <circle cx="11" cy="11" r="8" />
    <path d="m21 21-4.3-4.3" />
  </svg>
);

const IconPlus = ({ className = 'w-4 h-4' }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
    <path d="M5 12h14" />
    <path d="M12 5v14" />
  </svg>
);

const IconX = ({ className = 'w-3.5 h-3.5' }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
    <path d="M18 6 6 18" />
    <path d="m6 6 12 12" />
  </svg>
);

const IconChevron = ({ className = 'w-4 h-4' }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
    <path d="m6 9 6 6 6-6" />
  </svg>
);

const IconStar = ({ className = 'w-3.5 h-3.5' }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden="true">
    <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
  </svg>
);

const IconThumbUp = ({ className = 'w-3.5 h-3.5', filled = false }: { className?: string; filled?: boolean }) => (
  <svg viewBox="0 0 24 24" fill={filled ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
    <path d="M7 10v12" />
    <path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z" />
  </svg>
);

const IconThumbDown = ({ className = 'w-3.5 h-3.5', filled = false }: { className?: string; filled?: boolean }) => (
  <svg viewBox="0 0 24 24" fill={filled ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
    <path d="M17 14V2" />
    <path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z" />
  </svg>
);

const IconEye = ({ className = 'w-4 h-4' }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
    <path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0" />
    <circle cx="12" cy="12" r="3" />
  </svg>
);

const IconCheck = ({ className = 'w-3 h-3' }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
    <path d="M20 6 9 17l-5-5" />
  </svg>
);

const IconAlert = ({ className = 'w-4 h-4' }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
    <circle cx="12" cy="12" r="10" />
    <line x1="12" x2="12" y1="8" y2="12" />
    <line x1="12" x2="12.01" y1="16" y2="16" />
  </svg>
);

const IconSun = ({ className = 'w-4 h-4' }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2" />
    <path d="M12 20v2" />
    <path d="m4.93 4.93 1.41 1.41" />
    <path d="m17.66 17.66 1.41 1.41" />
    <path d="M2 12h2" />
    <path d="M20 12h2" />
    <path d="m6.34 17.66-1.41 1.41" />
    <path d="m19.07 4.93-1.41 1.41" />
  </svg>
);

const IconMoon = ({ className = 'w-4 h-4' }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
    <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" />
  </svg>
);

const IconSend = ({ className = 'w-3.5 h-3.5' }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
    <path d="m22 2-7 20-4-9-9-4Z" />
    <path d="M22 2 11 13" />
  </svg>
);

const IconArrowLeft = ({ className = 'w-4 h-4' }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
    <path d="m12 19-7-7 7-7" />
    <path d="M19 12H5" />
  </svg>
);

const IconArrowRight = ({ className = 'w-4 h-4' }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
    <path d="M5 12h14" />
    <path d="m12 5 7 7-7 7" />
  </svg>
);

// ============================================================================
// 5B. COMPONENT: JUSTWATCH-STYLE ANGLED BLURRED POSTER HERO BACKDROP
// Replicates the exact JustWatch hero look: oversized tilted poster grid,
// soft optical Gaussian blur on the poster layer, and a 3-layer dark scrim.
// ============================================================================

const JustWatchHeroBackdrop: React.FC<{ isDark: boolean }> = React.memo(({ isDark }) => {
  // 6 columns x 3 posters each = 18 posters filling the tilted viewport
  const columns = [0, 1, 2, 3, 4, 5];

  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 overflow-hidden select-none"
    >
      {/* Tilted & softly blurred poster mosaic (matches JustWatch ~-11deg lean) */}
      <div
        className="absolute -inset-x-24 -inset-y-20 flex justify-center gap-3.5 sm:gap-4"
        style={{
          transform: 'rotate(-11deg) scale(1.22)',
          transformOrigin: 'center center',
          filter: isDark
            ? 'blur(3.5px) brightness(0.62) saturate(1.18)'
            : 'blur(4px) brightness(0.92) saturate(1.05)',
          opacity: isDark ? 0.92 : 0.32,
        }}
      >
        {columns.map((colIdx) => {
          const offsetClass = colIdx % 2 === 0 ? '-translate-y-8' : 'translate-y-6';
          const items = [
            HERO_POSTER_WALL[(colIdx * 3) % HERO_POSTER_WALL.length],
            HERO_POSTER_WALL[(colIdx * 3 + 1) % HERO_POSTER_WALL.length],
            HERO_POSTER_WALL[(colIdx * 3 + 2) % HERO_POSTER_WALL.length],
          ];
          return (
            <div
              key={colIdx}
              className={`flex flex-col gap-3 sm:gap-3.5 w-32 sm:w-36 md:w-40 lg:w-44 shrink-0 ${offsetClass}`}
            >
              {items.map((src, idx) => (
                <div
                  key={`${colIdx}-${idx}`}
                  className="aspect-[3/4] w-full overflow-hidden rounded-2xl bg-[#12141B] shadow-[0_10px_30px_rgba(0,0,0,0.65)] ring-1 ring-white/10"
                >
                  <img
                    src={src}
                    alt=""
                    loading="eager"
                    decoding="async"
                    className="h-full w-full object-cover"
                  />
                </div>
              ))}
            </div>
          );
        })}
      </div>

      {/* Layer 1: Radial center spotlight scrim so headline & search box pop */}
      <div
        className="absolute inset-0"
        style={{
          background: isDark
            ? 'radial-gradient(ellipse 72% 64% at 50% 48%, rgba(10, 11, 15, 0.72) 0%, rgba(10, 11, 15, 0.48) 55%, rgba(10, 11, 15, 0.90) 100%)'
            : 'radial-gradient(ellipse 75% 65% at 50% 48%, rgba(248, 247, 244, 0.86) 0%, rgba(248, 247, 244, 0.68) 60%, rgba(248, 247, 244, 0.94) 100%)',
        }}
      />

      {/* Layer 2: Top navbar fade */}
      <div
        className="absolute inset-x-0 top-0 h-28"
        style={{
          background: isDark
            ? 'linear-gradient(to bottom, rgba(10, 11, 15, 0.92) 0%, rgba(10, 11, 15, 0) 100%)'
            : 'linear-gradient(to bottom, rgba(248, 247, 244, 0.92) 0%, rgba(248, 247, 244, 0) 100%)',
        }}
      />

      {/* Layer 3: Seamless bottom dissolve into page background */}
      <div
        className="absolute inset-x-0 bottom-0 h-32"
        style={{
          background: 'linear-gradient(to top, var(--bg) 0%, transparent 100%)',
        }}
      />
    </div>
  );
});

// ============================================================================
// 6. COMPONENT: SEARCH BOX (maps to src/components/SearchBox.tsx)
// ============================================================================

interface SearchBoxProps {
  text: string;
  onTextChange: (val: string) => void;
  seedTitles: string[];
  onSeedTitlesChange: (titles: string[]) => void;
  onSubmit: () => void;
  loading: boolean;
}

const SearchBox: React.FC<SearchBoxProps> = ({
  text,
  onTextChange,
  seedTitles,
  onSeedTitlesChange,
  onSubmit,
  loading,
}) => {
  const [placeholderIdx, setPlaceholderIdx] = useState(0);
  const [seedsOpen, setSeedsOpen] = useState(false);
  const [seedInput, setSeedInput] = useState('');

  // Rotate between the 2 example requests every 4.5s
  useEffect(() => {
    const id = setInterval(() => {
      setPlaceholderIdx((prev) => (prev + 1) % EXAMPLE_PLACEHOLDERS.length);
    }, 4500);
    return () => clearInterval(id);
  }, []);

  const handleTextareaKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (text.trim() || seedTitles.length > 0) {
        onSubmit();
      }
    }
  };

  const addSeedTitle = () => {
    const trimmed = seedInput.trim();
    if (!trimmed || seedTitles.length >= 3) return;
    if (seedTitles.some((t) => t.toLowerCase() === trimmed.toLowerCase())) {
      setSeedInput('');
      return;
    }
    onSeedTitlesChange([...seedTitles, trimmed]);
    setSeedInput('');
  };

  const handleSeedKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      addSeedTitle();
    }
  };

  const removeSeedTitle = (title: string) => {
    onSeedTitlesChange(seedTitles.filter((t) => t !== title));
  };

  const handleFormSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (text.trim() || seedTitles.length > 0) {
      onSubmit();
    }
  };

  const canSubmit = (text.trim().length > 0 || seedTitles.length > 0) && !loading;

  return (
    <form onSubmit={handleFormSubmit} className="w-full" aria-label="Recommendation search">
      <div className="rounded-2xl border border-[var(--border)] bg-[var(--surface)] shadow-[0_2px_16px_rgba(0,0,0,0.04)] dark:shadow-[0_4px_24px_rgba(0,0,0,0.35)] transition-colors focus-within:border-[var(--accent)] focus-within:ring-2 focus-within:ring-[var(--accent-ring)]">
        <label htmlFor="recommend-query" className="sr-only">
          Describe the manga or manhwa you want to read
        </label>
        <textarea
          id="recommend-query"
          rows={3}
          value={text}
          onChange={(e) => onTextChange(e.target.value)}
          onKeyDown={handleTextareaKeyDown}
          placeholder={`Try: "${EXAMPLE_PLACEHOLDERS[placeholderIdx]}"`}
          className="block w-full resize-none bg-transparent px-4 pt-4 pb-2 text-[15px] sm:text-base leading-relaxed text-[var(--ink)] placeholder:text-[var(--ink-subtle)] focus:outline-none"
        />

        {/* Bottom action bar inside textarea container */}
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[var(--border-subtle)] px-3 py-2.5">
          <button
            type="button"
            onClick={() => setSeedsOpen((prev) => !prev)}
            aria-expanded={seedsOpen}
            aria-controls="seed-titles-panel"
            className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium text-[var(--ink-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--ink)] transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            <IconPlus className={`w-3.5 h-3.5 transition-transform ${seedsOpen ? 'rotate-45' : ''}`} />
            <span>I liked...</span>
            {seedTitles.length > 0 && (
              <span className="ml-0.5 rounded-full bg-[var(--accent-soft)] px-1.5 py-0.5 font-mono text-[11px] font-semibold text-[var(--accent)]">
                {seedTitles.length}/3
              </span>
            )}
          </button>

          <div className="flex items-center gap-3">
            <span className="hidden sm:inline text-[11px] text-[var(--ink-subtle)]">
              Enter to search · Shift+Enter for newline
            </span>
            <button
              type="submit"
              disabled={!canSubmit}
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-white shadow-sm hover:opacity-95 active:scale-[0.99] disabled:opacity-45 disabled:cursor-not-allowed transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--surface)]"
            >
              <IconSearch className="w-4 h-4" />
              <span>{loading ? 'Searching…' : 'Search'}</span>
            </button>
          </div>
        </div>

        {/* Collapsible "I liked..." Seed Titles Panel */}
        {seedsOpen && (
          <div
            id="seed-titles-panel"
            className="border-t border-[var(--border-subtle)] bg-[var(--bg-subtle)] px-3.5 py-3 rounded-b-2xl"
          >
            <div className="flex flex-col sm:flex-row sm:items-center gap-2">
              <label htmlFor="seed-title-input" className="sr-only">
                Add up to 3 titles you liked
              </label>
              <input
                id="seed-title-input"
                type="text"
                value={seedInput}
                onChange={(e) => setSeedInput(e.target.value)}
                onKeyDown={handleSeedKeyDown}
                disabled={seedTitles.length >= 3}
                placeholder={
                  seedTitles.length >= 3
                    ? 'Maximum 3 titles added'
                    : 'Type a title you loved and press Enter (max 3)'
                }
                className="flex-1 rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-1.5 text-sm text-[var(--ink)] placeholder:text-[var(--ink-subtle)] disabled:opacity-50 focus:border-[var(--accent)] focus:outline-none focus:ring-2 focus:ring-[var(--accent-ring)]"
              />
              <button
                type="button"
                onClick={addSeedTitle}
                disabled={!seedInput.trim() || seedTitles.length >= 3}
                className="rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-1.5 text-xs font-semibold text-[var(--ink)] hover:bg-[var(--surface-hover)] disabled:opacity-40 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              >
                Add title
              </button>
            </div>

            {seedTitles.length > 0 && (
              <div className="mt-2.5 flex flex-wrap gap-1.5" aria-label="Liked titles">
                {seedTitles.map((title) => (
                  <span
                    key={title}
                    className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border)] bg-[var(--surface)] pl-3 pr-1.5 py-1 text-xs font-medium text-[var(--ink)]"
                  >
                    <span>{title}</span>
                    <button
                      type="button"
                      onClick={() => removeSeedTitle(title)}
                      aria-label={`Remove ${title}`}
                      className="rounded-full p-0.5 text-[var(--ink-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--ink)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                    >
                      <IconX className="w-3 h-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </form>
  );
};

// ============================================================================
// 7. COMPONENT: STICKY FILTER BAR (maps to src/components/FilterBar.tsx)
// ============================================================================

interface FilterBarProps {
  filters: Filters;
  onChangeFilter: (patch: Partial<Filters>) => void;
  onToggleMature: (nextVal: boolean) => void;
  refiltering: boolean;
}

const TYPE_OPTIONS: { value: SeriesTypeFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'manhwa', label: 'Manhwa' },
  { value: 'manga', label: 'Manga' },
  { value: 'manhua', label: 'Manhua' },
];

const FilterBar: React.FC<FilterBarProps> = ({
  filters,
  onChangeFilter,
  onToggleMature,
  refiltering,
}) => {
  return (
    <div
      role="region"
      aria-label="Filter results"
      className="sticky top-0 z-30 -mx-4 px-4 sm:-mx-6 sm:px-6 py-3 border-y border-[var(--border)] bg-[var(--bg-glass)] backdrop-blur-md transition-colors"
    >
      <div className="mx-auto max-w-7xl flex flex-col lg:flex-row lg:items-center lg:justify-between gap-3">
        {/* Segmented Control: All / Manhwa / Manga / Manhua */}
        <div
          role="radiogroup"
          aria-label="Series origin type"
          className="inline-flex w-full sm:w-auto rounded-xl border border-[var(--border)] bg-[var(--bg-subtle)] p-1"
        >
          {TYPE_OPTIONS.map((opt) => {
            const active = filters.type === opt.value;
            return (
              <button
                key={opt.value}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => onChangeFilter({ type: opt.value })}
                className={`flex-1 sm:flex-initial rounded-lg px-3 py-1.5 text-xs font-semibold transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] ${
                  active
                    ? 'bg-[var(--surface)] text-[var(--ink)] shadow-sm'
                    : 'text-[var(--ink-muted)] hover:text-[var(--ink)]'
                }`}
              >
                {opt.label}
              </button>
            );
          })}
        </div>

        {/* Toggles + Format Dropdown */}
        <div className="flex flex-wrap items-center gap-2 sm:gap-3">
          {/* Format Select */}
          <div className="relative">
            <label htmlFor="format-filter" className="sr-only">
              Series format
            </label>
            <select
              id="format-filter"
              value={filters.format}
              onChange={(e) =>
                onChangeFilter({ format: e.target.value as SeriesFormatFilter })
              }
              className="appearance-none rounded-lg border border-[var(--border)] bg-[var(--surface)] pl-3 pr-8 py-1.5 text-xs font-medium text-[var(--ink)] hover:border-[var(--ink-subtle)] focus:border-[var(--accent)] focus:outline-none focus:ring-2 focus:ring-[var(--accent-ring)]"
            >
              <option value="any">Any format</option>
              <option value="oneshot">One-shot</option>
              <option value="short_series">Short series</option>
              <option value="series">Full series</option>
            </select>
            <IconChevron className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[var(--ink-muted)]" />
          </div>

          {/* Toggle: Official English only */}
          <button
            type="button"
            role="switch"
            aria-checked={filters.readable_only}
            onClick={() => onChangeFilter({ readable_only: !filters.readable_only })}
            className={`inline-flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] ${
              filters.readable_only
                ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
                : 'border-[var(--border)] bg-[var(--surface)] text-[var(--ink-muted)] hover:text-[var(--ink)]'
            }`}
          >
            <span
              className={`h-3.5 w-6 rounded-full p-0.5 transition-colors flex items-center ${
                filters.readable_only ? 'bg-emerald-600 justify-end' : 'bg-[var(--border)] justify-start'
              }`}
            >
              <span className="h-2.5 w-2.5 rounded-full bg-white shadow-xs" />
            </span>
            <span>Official English only</span>
          </button>

          {/* Toggle: Include suggestive */}
          <button
            type="button"
            role="switch"
            aria-checked={filters.include_suggestive}
            onClick={() =>
              onChangeFilter({ include_suggestive: !filters.include_suggestive })
            }
            className={`inline-flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] ${
              filters.include_suggestive
                ? 'border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-300'
                : 'border-[var(--border)] bg-[var(--surface)] text-[var(--ink-muted)] hover:text-[var(--ink)]'
            }`}
          >
            <span
              className={`h-3.5 w-6 rounded-full p-0.5 transition-colors flex items-center ${
                filters.include_suggestive
                  ? 'bg-amber-500 justify-end'
                  : 'bg-[var(--border)] justify-start'
              }`}
            >
              <span className="h-2.5 w-2.5 rounded-full bg-white shadow-xs" />
            </span>
            <span>Include suggestive</span>
          </button>

          {/* Toggle: 18+ content */}
          <button
            type="button"
            role="switch"
            aria-checked={filters.include_mature}
            onClick={() => onToggleMature(!filters.include_mature)}
            className={`inline-flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-rose-500 ${
              filters.include_mature
                ? 'border-rose-500/45 bg-rose-500/10 text-rose-700 dark:text-rose-300'
                : 'border-[var(--border)] bg-[var(--surface)] text-[var(--ink-muted)] hover:text-[var(--ink)]'
            }`}
          >
            <span
              className={`h-3.5 w-6 rounded-full p-0.5 transition-colors flex items-center ${
                filters.include_mature ? 'bg-rose-600 justify-end' : 'bg-[var(--border)] justify-start'
              }`}
            >
              <span className="h-2.5 w-2.5 rounded-full bg-white shadow-xs" />
            </span>
            <span>18+ content</span>
          </button>

          {refiltering && (
            <span className="text-[11px] font-mono text-[var(--accent)] animate-pulse">
              Updating…
            </span>
          )}
        </div>
      </div>
    </div>
  );
};

// ============================================================================
// 8. COMPONENT: 18+ AGE GATE MODAL (maps to src/components/AgeGateModal.tsx)
// ============================================================================

interface AgeGateModalProps {
  open: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

const AgeGateModal: React.FC<AgeGateModalProps> = ({ open, onConfirm, onCancel }) => {
  const confirmBtnRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    confirmBtnRef.current?.focus();
    const onKeyDown = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape') onCancel();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="age-gate-title"
      aria-describedby="age-gate-desc"
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-xs"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-sm rounded-2xl border border-[var(--border)] bg-[var(--surface)] p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="inline-flex items-center rounded-md bg-rose-500/15 px-2 py-0.5 text-[11px] font-bold uppercase tracking-wider text-rose-600 dark:text-rose-400">
          Adult Content Opt-In
        </div>
        <h2 id="age-gate-title" className="mt-3 text-lg font-bold text-[var(--ink)]">
          Confirm you are 18 or older
        </h2>
        <p id="age-gate-desc" className="mt-2 text-sm leading-relaxed text-[var(--ink-muted)]">
          Enabling 18+ content includes mature and erotica-rated titles in search results. Mature
          covers always stay blurred until you tap to reveal them.
        </p>
        <div className="mt-6 flex items-center justify-end gap-2.5">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-xl border border-[var(--border)] px-4 py-2 text-xs font-semibold text-[var(--ink-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--ink)] transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            Cancel
          </button>
          <button
            ref={confirmBtnRef}
            type="button"
            onClick={onConfirm}
            className="rounded-xl bg-rose-600 px-4 py-2 text-xs font-semibold text-white hover:bg-rose-500 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-rose-500 focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--surface)]"
          >
            I am 18 or older
          </button>
        </div>
      </div>
    </div>
  );
};

// ============================================================================
// 9. COMPONENT: INTENT CHIPS (maps to src/components/IntentChips.tsx)
// ============================================================================

interface IntentChipsProps {
  intent: IntentSummary;
  latency: { llm: number; db: number };
  total: number;
}

const IntentChips: React.FC<IntentChipsProps> = ({ intent, latency, total }) => {
  const hasChips = intent.must.length > 0 || intent.boosts.length > 0;
  if (!hasChips) return null;

  return (
    <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2.5 py-1">
      <div className="flex flex-wrap items-center gap-1.5" aria-label="Understood request tags">
        <span className="mr-1 text-[11px] font-semibold uppercase tracking-wider text-[var(--ink-subtle)]">
          Understood as
        </span>

        {/* intent.must -> filled chips (read-only) */}
        {intent.must.map((tag) => (
          <span
            key={`must-${tag}`}
            className="inline-flex items-center rounded-full bg-[var(--accent)] px-2.5 py-0.5 text-xs font-medium text-white"
          >
            {tag}
          </span>
        ))}

        {/* intent.boosts -> outlined chips (read-only) */}
        {intent.boosts.map((tag) => (
          <span
            key={`boost-${tag}`}
            className="inline-flex items-center rounded-full border border-[var(--border)] bg-[var(--surface)] px-2.5 py-0.5 text-xs font-medium text-[var(--ink-muted)]"
          >
            {tag}
          </span>
        ))}
      </div>

      <div className="font-mono text-[11px] text-[var(--ink-subtle)] shrink-0">
        {total} {total === 1 ? 'title' : 'titles'}
        {latency.llm > 0
          ? ` · ${(latency.llm / 1000).toFixed(1)}s AI / ${latency.db}ms DB`
          : ` · ${latency.db}ms DB`}
      </div>
    </div>
  );
};

// ============================================================================
// 10. COMPONENT: NOTICE & ERROR BANNERS (maps to src/components/NoticeBanners.tsx)
// ============================================================================

interface NoticeBannersProps {
  error: ApiClientError | null;
  onRetry: () => void;
  degraded: boolean;
  notFoundTitles: string[];
  skippedTitles: string[];
}

const NoticeBanners: React.FC<NoticeBannersProps> = ({
  error,
  onRetry,
  degraded,
  notFoundTitles,
  skippedTitles,
}) => {
  return (
    <div className="space-y-2.5">
      {/* Error state (429 rate limit, network error, or server error) */}
      {error && (
        <div
          role="alert"
          className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-xl border border-rose-500/35 bg-rose-500/10 px-4 py-3 text-sm text-rose-800 dark:text-rose-200"
        >
          <div className="flex items-start gap-2.5">
            <IconAlert className="w-4 h-4 mt-0.5 shrink-0 text-rose-600 dark:text-rose-400" />
            <div>
              <p className="font-semibold">
                {error.kind === 'rate_limit'
                  ? 'Rate limit reached (429)'
                  : error.kind === 'network'
                  ? 'Connection error'
                  : 'Request failed'}
              </p>
              <p className="text-xs opacity-90 mt-0.5">{error.message}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onRetry}
            className="self-start sm:self-center shrink-0 rounded-lg bg-rose-600 px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-rose-500 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-rose-500"
          >
            Retry
          </button>
        </div>
      )}

      {/* Degraded keyword-only mode notice */}
      {degraded && (
        <div
          role="status"
          className="flex items-center gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 px-3.5 py-2 text-xs text-amber-800 dark:text-amber-200"
        >
          <IconAlert className="w-3.5 h-3.5 shrink-0" />
          <span>Results are keyword-based right now while semantic matching recovers.</span>
        </div>
      )}

      {/* Not found seed titles */}
      {notFoundTitles.length > 0 && (
        <div
          role="status"
          className="rounded-xl border border-[var(--border)] bg-[var(--bg-subtle)] px-3.5 py-2 text-xs text-[var(--ink-muted)]"
        >
          Couldn&apos;t find:{' '}
          <span className="font-semibold text-[var(--ink)]">{notFoundTitles.join(', ')}</span>,
          continuing without {notFoundTitles.length === 1 ? 'it' : 'them'}.
        </div>
      )}

      {/* Skipped titles notice */}
      {skippedTitles.length > 0 && (
        <div
          role="status"
          className="rounded-xl border border-[var(--border)] bg-[var(--bg-subtle)] px-3.5 py-2 text-xs text-[var(--ink-muted)]"
        >
          Skipped:{' '}
          <span className="font-semibold text-[var(--ink)]">{skippedTitles.join(', ')}</span>.
        </div>
      )}
    </div>
  );
};

// ============================================================================
// 11. COMPONENT: SERIES CARD (maps to src/components/SeriesCard.tsx)
// ============================================================================

interface SeriesCardProps {
  series: SeriesItem;
  vote: 1 | -1 | undefined;
  savedNote: string | undefined;
  onVote: (seriesId: number, vote: 1 | -1) => void;
  onSaveNote: (seriesId: number, note: string) => void;
}

const SeriesCard: React.FC<SeriesCardProps> = React.memo(
  ({ series, vote, savedNote, onVote, onSaveNote }) => {
    const [imgFailed, setImgFailed] = useState(false);
    const [synopsisExpanded, setSynopsisExpanded] = useState(false);
    const [revealed, setRevealed] = useState(false);
    const [noteDraft, setNoteDraft] = useState('');

    const isMatureOrSuggestive =
      series.content_rating === 'erotica' || series.content_rating === 'suggestive';
    const isBlurred = isMatureOrSuggestive && !revealed;

    const typeLabel =
      series.type === 'manhwa' ? 'Manhwa' : series.type === 'manga' ? 'Manga' : 'Manhua';

    const searchUrl = `https://www.google.com/search?q=${encodeURIComponent(
      `${series.title} read`
    )}`;

    const handleCardClick = (e: React.MouseEvent<HTMLElement>) => {
      if (isBlurred) return;
      const target = e.target as HTMLElement | null;
      if (target?.closest('button, a, input, form, textarea, select')) return;
      if (window.getSelection()?.toString()) return;
      window.open(searchUrl, '_blank', 'noopener,noreferrer');
    };

    const handleNoteSubmit = (e: FormEvent) => {
      e.preventDefault();
      if (!noteDraft.trim()) return;
      onSaveNote(series.id, noteDraft);
      setNoteDraft('');
    };

    return (
      <article
        onClick={handleCardClick}
        className={`group flex flex-col overflow-hidden rounded-2xl border border-[var(--border)] bg-[var(--surface)] transition-all hover:border-[var(--ink-subtle)]/50 hover:shadow-[0_8px_24px_rgba(0,0,0,0.06)] dark:hover:shadow-[0_8px_24px_rgba(0,0,0,0.35)] ${
          isBlurred ? '' : 'cursor-pointer'
        }`}
      >
        {/* Balanced 4:5 Cover Container */}
        <div className="relative aspect-[4/5] max-h-72 sm:max-h-none w-full overflow-hidden bg-[var(--bg-subtle)]">
          {!isBlurred ? (
            <a
              href={searchUrl}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={`Search Google for ${series.title} read`}
              className="block h-full w-full focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--accent)]"
            >
              {series.cover && !imgFailed ? (
                <img
                  src={series.cover}
                  alt={`Cover of ${series.title}`}
                  loading="lazy"
                  onError={() => setImgFailed(true)}
                  className="h-full w-full object-cover transition-all duration-300 group-hover:scale-[1.03]"
                />
              ) : (
                <div className="flex h-full w-full flex-col items-center justify-center p-4 text-center bg-gradient-to-br from-[var(--bg-subtle)] to-[var(--border-subtle)]">
                  <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--ink-subtle)]">
                    {typeLabel}
                  </span>
                  <p className="mt-1.5 text-sm font-semibold text-[var(--ink-muted)] line-clamp-3">
                    {series.title}
                  </p>
                </div>
              )}
            </a>
          ) : series.cover && !imgFailed ? (
            <img
              src={series.cover}
              alt={`Cover of ${series.title}`}
              loading="lazy"
              onError={() => setImgFailed(true)}
              className="h-full w-full object-cover transition-all duration-300 blur-2xl scale-110 brightness-75"
            />
          ) : (
            <div className="flex h-full w-full flex-col items-center justify-center p-4 text-center bg-gradient-to-br from-[var(--bg-subtle)] to-[var(--border-subtle)]">
              <span className="font-mono text-[10px] uppercase tracking-widest text-[var(--ink-subtle)]">
                {typeLabel}
              </span>
              <p className="mt-1.5 text-sm font-semibold text-[var(--ink-muted)] line-clamp-3">
                {series.title}
              </p>
            </div>
          )}

          {/* Top-left badges: Type + Mature/Suggestive */}
          <div className="absolute top-2.5 left-2.5 flex flex-wrap items-center gap-1.5">
            <span className="rounded-md bg-black/70 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-white backdrop-blur-xs">
              {typeLabel}
            </span>
            {isMatureOrSuggestive && (
              <span
                className={`rounded-md px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-white backdrop-blur-xs ${
                  series.content_rating === 'erotica'
                    ? 'bg-rose-600/90'
                    : 'bg-amber-600/90'
                }`}
              >
                {series.content_rating === 'erotica' ? '18+' : 'Suggestive'}
              </span>
            )}
          </div>

          {/* Top-right rating pill */}
          {series.rating !== null && (
            <div className="absolute top-2.5 right-2.5 inline-flex items-center gap-1 rounded-md bg-black/70 px-1.5 py-0.5 font-mono text-[11px] font-semibold text-amber-300 backdrop-blur-xs">
              <IconStar className="w-3 h-3" />
              <span>{series.rating.toFixed(1)}</span>
            </div>
          )}

          {/* Tap-to-reveal overlay for blurred mature/suggestive covers */}
          {isBlurred && (
            <button
              type="button"
              onClick={() => setRevealed(true)}
              aria-label={`Reveal ${series.content_rating} cover for ${series.title}`}
              className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/35 p-4 text-white transition-colors hover:bg-black/45 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-white"
            >
              <span className="inline-flex items-center gap-1.5 rounded-full bg-black/75 px-3 py-1.5 text-xs font-semibold backdrop-blur-sm">
                <IconEye className="w-3.5 h-3.5" />
                Tap to reveal
              </span>
              <span className="text-[11px] text-white/80">
                {series.content_rating === 'erotica' ? '18+ Mature Cover' : 'Suggestive Cover'}
              </span>
            </button>
          )}
        </div>

        {/* Card Body */}
        <div className="flex flex-1 flex-col p-3.5">
          {/* Title & Metadata */}
          <h3 className="text-[15px] font-bold leading-snug text-[var(--ink)] line-clamp-2">
            <a
              href={searchUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-[var(--accent)] transition-colors focus:outline-none focus-visible:underline"
            >
              {series.title}
            </a>
          </h3>

          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-[var(--ink-muted)]">
            {series.year && <span className="font-mono">{series.year}</span>}
            {series.status && (
              <>
                <span aria-hidden="true">·</span>
                <span>{series.status}</span>
              </>
            )}
          </div>

          {/* Official English Badge */}
          {series.readable && (
            <div className="mt-2">
              <span className="inline-flex items-center gap-1 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[11px] font-semibold text-emerald-700 dark:text-emerald-300">
                <IconCheck className="w-3 h-3" />
                Official English
              </span>
            </div>
          )}

          {/* 2-Line Synopsis Clamp (Expands on Click) */}
          {series.synopsis && (
            <button
              type="button"
              onClick={() => setSynopsisExpanded((prev) => !prev)}
              aria-expanded={synopsisExpanded}
              className="mt-2.5 text-left text-xs leading-relaxed text-[var(--ink-muted)] hover:text-[var(--ink)] transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] rounded"
            >
              <span className={synopsisExpanded ? '' : 'line-clamp-2'}>
                {series.synopsis}
              </span>
            </button>
          )}

          {/* Matched Tags */}
          {series.matched_tags.length > 0 && (
            <div className="mt-2.5 flex flex-wrap gap-1">
              {series.matched_tags.slice(0, 4).map((tag) => (
                <span
                  key={tag}
                  className="rounded-md bg-[var(--bg-subtle)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--ink-muted)]"
                >
                  {tag}
                </span>
              ))}
            </div>
          )}

          {/* Footer: Optimistic Thumbs Up / Down + Optional 1-Line Comment */}
          <div className="mt-auto pt-3.5">
            <div className="flex items-center justify-between border-t border-[var(--border-subtle)] pt-2.5">
              <span className="text-[11px] text-[var(--ink-subtle)]">
                {vote === 1
                  ? 'Marked relevant'
                  : vote === -1
                  ? 'Marked not a fit'
                  : 'Good match?'}
              </span>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  disabled={vote !== undefined}
                  onClick={() => onVote(series.id, 1)}
                  aria-label={`Thumbs up for ${series.title}`}
                  aria-pressed={vote === 1}
                  className={`rounded-lg p-1.5 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] ${
                    vote === 1
                      ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                      : vote === -1
                      ? 'text-[var(--ink-subtle)] opacity-35 cursor-not-allowed'
                      : 'text-[var(--ink-muted)] hover:bg-[var(--surface-hover)] hover:text-emerald-600 dark:hover:text-emerald-400'
                  }`}
                >
                  <IconThumbUp filled={vote === 1} />
                </button>
                <button
                  type="button"
                  disabled={vote !== undefined}
                  onClick={() => onVote(series.id, -1)}
                  aria-label={`Thumbs down for ${series.title}`}
                  aria-pressed={vote === -1}
                  className={`rounded-lg p-1.5 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] ${
                    vote === -1
                      ? 'bg-rose-500/15 text-rose-600 dark:text-rose-400'
                      : vote === 1
                      ? 'text-[var(--ink-subtle)] opacity-35 cursor-not-allowed'
                      : 'text-[var(--ink-muted)] hover:bg-[var(--surface-hover)] hover:text-rose-600 dark:hover:text-rose-400'
                  }`}
                >
                  <IconThumbDown filled={vote === -1} />
                </button>
              </div>
            </div>

            {/* Optional one-line comment after voting (sends note to POST /feedback) */}
            {vote !== undefined && (
              <div className="mt-2">
                {savedNote ? (
                  <p className="flex items-center gap-1.5 rounded-lg bg-[var(--bg-subtle)] px-2.5 py-1.5 text-[11px] text-[var(--ink-muted)]">
                    <IconCheck className="w-3 h-3 shrink-0 text-emerald-500" />
                    <span className="truncate">&ldquo;{savedNote}&rdquo;</span>
                  </p>
                ) : (
                  <form onSubmit={handleNoteSubmit} className="flex items-center gap-1.5">
                    <label htmlFor={`note-${series.id}`} className="sr-only">
                      Optional one-line comment for {series.title}
                    </label>
                    <input
                      id={`note-${series.id}`}
                      type="text"
                      maxLength={140}
                      value={noteDraft}
                      onChange={(e) => setNoteDraft(e.target.value)}
                      placeholder="Optional 1-line comment…"
                      className="min-w-0 flex-1 rounded-lg border border-[var(--border)] bg-[var(--bg-subtle)] px-2.5 py-1 text-[11px] text-[var(--ink)] placeholder:text-[var(--ink-subtle)] focus:border-[var(--accent)] focus:outline-none focus:ring-1 focus:ring-[var(--accent-ring)]"
                    />
                    <button
                      type="submit"
                      disabled={!noteDraft.trim()}
                      aria-label="Send comment"
                      className="inline-flex items-center justify-center rounded-lg bg-[var(--accent)] p-1.5 text-white hover:opacity-95 disabled:opacity-35 transition-opacity focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                    >
                      <IconSend className="w-3 h-3" />
                    </button>
                  </form>
                )}
              </div>
            )}
          </div>
        </div>
      </article>
    );
  }
);

// ============================================================================
// 12. COMPONENT: SKELETON GRID & EMPTY STATE (src/components/SkeletonGrid.tsx)
// ============================================================================

const SkeletonGrid: React.FC = () => (
  <div
    aria-hidden="true"
    className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-5"
  >
    {Array.from({ length: 13 }).map((_, i) => (
      <div
        key={i}
        className="overflow-hidden rounded-2xl border border-[var(--border)] bg-[var(--surface)] animate-pulse"
      >
        <div className="aspect-[4/5] max-h-72 sm:max-h-none w-full bg-[var(--bg-subtle)]" />
        <div className="p-3.5 space-y-2.5">
          <div className="h-4 w-4/5 rounded bg-[var(--bg-subtle)]" />
          <div className="h-3 w-1/3 rounded bg-[var(--bg-subtle)]" />
          <div className="h-3 w-full rounded bg-[var(--bg-subtle)]" />
          <div className="h-3 w-5/6 rounded bg-[var(--bg-subtle)]" />
          <div className="flex gap-1.5 pt-1">
            <div className="h-5 w-14 rounded bg-[var(--bg-subtle)]" />
            <div className="h-5 w-16 rounded bg-[var(--bg-subtle)]" />
          </div>
        </div>
      </div>
    ))}
  </div>
);

const EmptyState: React.FC<{ hasActiveFilters: boolean; onResetFilters: () => void }> = ({
  hasActiveFilters,
  onResetFilters,
}) => (
  <div className="rounded-2xl border border-dashed border-[var(--border)] bg-[var(--surface)] px-6 py-14 text-center">
    <p className="text-base font-semibold text-[var(--ink)]">No matching titles found</p>
    <p className="mt-1.5 text-sm text-[var(--ink-muted)] max-w-md mx-auto">
      {hasActiveFilters
        ? 'Your current filters may be too strict for this request. Try removing a filter or switching back to All types.'
        : 'Try describing a broader mood, trope, or adding a title you liked.'}
    </p>
    {hasActiveFilters && (
      <button
        type="button"
        onClick={onResetFilters}
        className="mt-5 rounded-xl bg-[var(--accent)] px-4 py-2 text-xs font-semibold text-white hover:opacity-95 transition-opacity focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
      >
        Remove filters
      </button>
    )}
  </div>
);

// ============================================================================
// 12B. NAVIGATION HELPERS & SHARED TOP NAV
// ============================================================================

function resolvePageFromLocation(): AppPage {
  if (typeof window === 'undefined') return 'home';
  const normalizedPath = window.location.pathname.replace(/\/+$/, '') || '/';
  if (normalizedPath === '/faqs' || window.location.hash === '#faqs') {
    return 'faqs';
  }
  return 'home';
}

interface NavAnchorProps {
  href: string;
  active?: boolean;
  onNavigate: () => void;
  className?: string;
  children: React.ReactNode;
}

const NavAnchor: React.FC<NavAnchorProps> = ({
  href,
  active,
  onNavigate,
  className = '',
  children,
}) => {
  const handleClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) {
      return;
    }
    e.preventDefault();
    onNavigate();
  };

  return (
    <a
      href={href}
      aria-current={active ? 'page' : undefined}
      onClick={handleClick}
      className={className}
    >
      {children}
    </a>
  );
};

interface TopNavProps {
  page: AppPage;
  onNavigate: (target: AppPage) => void;
  theme: 'light' | 'dark';
  onToggleTheme: () => void;
}

const TopNav: React.FC<TopNavProps> = ({ page, onNavigate, theme, onToggleTheme }) => {
  const navLinks: { id: AppPage; href: string; label: string }[] = [
    { id: 'home', href: '/', label: 'Discover' },
    { id: 'faqs', href: '/faqs', label: 'FAQs' },
  ];

  return (
    <nav
      aria-label="Primary"
      className="relative z-10 mx-auto flex w-full max-w-7xl items-center justify-between px-4 sm:px-6 pt-5 pb-6"
    >
      <NavAnchor
        href="/"
        onNavigate={() => onNavigate('home')}
        className="inline-flex items-center gap-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] rounded-lg"
      >
        <span className="text-xl sm:text-2xl font-extrabold tracking-tight text-[var(--ink)]">
          Koma<span className="text-[var(--accent)]">.</span>
        </span>
        <span className="hidden sm:inline text-xs font-medium text-[var(--ink-muted)]">
          Manga &amp; Manhwa Guide
        </span>
      </NavAnchor>

      <div className="flex items-center gap-2 sm:gap-3">
        <div className="flex items-center gap-1 rounded-xl border border-[var(--border)] bg-[var(--surface)]/75 p-1 backdrop-blur-md">
          {navLinks.map((item) => {
            const active = page === item.id;
            return (
              <NavAnchor
                key={item.id}
                href={item.href}
                active={active}
                onNavigate={() => onNavigate(item.id)}
                className={`rounded-lg px-2.5 sm:px-3 py-1 text-xs font-semibold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] ${
                  active
                    ? 'bg-[var(--accent)] text-white shadow-xs'
                    : 'text-[var(--ink-muted)] hover:text-[var(--ink)] hover:bg-[var(--surface-hover)]/70'
                }`}
              >
                {item.label}
              </NavAnchor>
            );
          })}
        </div>

        <button
          type="button"
          onClick={onToggleTheme}
          aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
          className="inline-flex items-center gap-1.5 rounded-xl border border-[var(--border)] bg-[var(--surface)]/80 backdrop-blur-md px-3 py-1.5 text-xs font-semibold text-[var(--ink)] hover:bg-[var(--surface-hover)] transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        >
          {theme === 'dark' ? (
            <>
              <IconSun className="w-3.5 h-3.5 text-amber-400" />
              <span>Light</span>
            </>
          ) : (
            <>
              <IconMoon className="w-3.5 h-3.5 text-indigo-500" />
              <span>Dark</span>
            </>
          )}
        </button>
      </div>
    </nav>
  );
};

// ============================================================================
// 12C. DEDICATED PAGE: FAQS (maps to src/pages/FaqPage.tsx)
// ============================================================================

interface FaqPageProps {
  onNavigate: (target: AppPage) => void;
  theme: 'light' | 'dark';
  onToggleTheme: () => void;
}

const FaqPage: React.FC<FaqPageProps> = ({ onNavigate, theme, onToggleTheme }) => {
  const [activeCategory, setActiveCategory] = useState<FaqCategoryId>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [openIds, setOpenIds] = useState<Set<string>>(() => new Set([FAQ_ITEMS[0].id]));

  const normalizedQuery = searchQuery.trim().toLowerCase();

  const visibleItems = FAQ_ITEMS.filter((item) => {
    if (activeCategory !== 'all' && item.category !== activeCategory) return false;
    if (!normalizedQuery) return true;
    return (
      item.question.toLowerCase().includes(normalizedQuery) ||
      item.answer.toLowerCase().includes(normalizedQuery)
    );
  });

  const groupedSections = FAQ_CATEGORIES.slice(1)
    .filter((cat) => activeCategory === 'all' || cat.id === activeCategory)
    .map((cat) => ({
      ...cat,
      items: visibleItems.filter((item) => item.category === cat.id),
    }))
    .filter((group) => group.items.length > 0);

  const allVisibleOpen =
    visibleItems.length > 0 && visibleItems.every((item) => openIds.has(item.id));

  const toggleItem = (id: string) => {
    setOpenIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const toggleAllVisible = () => {
    setOpenIds((prev) => {
      const next = new Set(prev);
      if (allVisibleOpen) {
        visibleItems.forEach((item) => next.delete(item.id));
      } else {
        visibleItems.forEach((item) => next.add(item.id));
      }
      return next;
    });
  };

  const resetFaqFilters = () => {
    setActiveCategory('all');
    setSearchQuery('');
  };

  return (
    <>
      <header className="relative overflow-hidden border-b border-[var(--border)] bg-[var(--bg-subtle)]/70 pb-10 sm:pb-12">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              theme === 'dark'
                ? 'radial-gradient(ellipse 65% 80% at 50% 0%, rgba(99, 102, 241, 0.16) 0%, rgba(10, 11, 15, 0) 75%)'
                : 'radial-gradient(ellipse 65% 80% at 50% 0%, rgba(79, 70, 229, 0.1) 0%, rgba(248, 247, 244, 0) 75%)',
          }}
        />

        <TopNav
          page="faqs"
          onNavigate={onNavigate}
          theme={theme}
          onToggleTheme={onToggleTheme}
        />

        <div className="relative z-10 mx-auto w-full max-w-4xl px-4 sm:px-6 pt-2 sm:pt-4">
          <NavAnchor
            href="/"
            onNavigate={() => onNavigate('home')}
            className="inline-flex items-center gap-1.5 text-xs font-semibold text-[var(--accent)] hover:opacity-85 transition-opacity focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] rounded"
          >
            <IconArrowLeft className="w-3.5 h-3.5" />
            <span>Back to recommender</span>
          </NavAnchor>

          <p className="mt-4 font-mono text-[11px] font-semibold uppercase tracking-widest text-[var(--accent)]">
            Help &amp; Documentation
          </p>
          <h1 className="mt-1.5 text-3xl sm:text-4xl font-extrabold tracking-tight text-[var(--ink)]">
            Frequently asked questions
          </h1>
          <p className="mt-2.5 text-sm sm:text-base text-[var(--ink-muted)] max-w-2xl">
            How Koma parses free-text prompts, re-filters the catalog without re-running AI,
            protects 18+ preferences, and opens reading links.
          </p>
        </div>
      </header>

      <main className="mx-auto w-full max-w-4xl flex-1 px-4 sm:px-6 py-8 sm:py-12">
        {/* FAQ Search & Category Filter Controls */}
        <div className="space-y-4 mb-8">
          <div className="relative">
            <label htmlFor="faq-search" className="sr-only">
              Search frequently asked questions
            </label>
            <IconSearch className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--ink-subtle)]" />
            <input
              id="faq-search"
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search questions (e.g. filters, 18+, seed titles, reading links)…"
              className="w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] pl-10 pr-10 py-2.5 text-sm text-[var(--ink)] placeholder:text-[var(--ink-subtle)] focus:border-[var(--accent)] focus:outline-none focus:ring-2 focus:ring-[var(--accent-ring)]"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery('')}
                aria-label="Clear FAQ search"
                className="absolute right-3 top-1/2 -translate-y-1/2 rounded-md p-1 text-[var(--ink-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--ink)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              >
                <IconX className="w-3.5 h-3.5" />
              </button>
            )}
          </div>

          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="FAQ topic categories">
              {FAQ_CATEGORIES.map((cat) => {
                const selected = activeCategory === cat.id;
                return (
                  <button
                    key={cat.id}
                    type="button"
                    aria-pressed={selected}
                    onClick={() => setActiveCategory(cat.id)}
                    className={`rounded-full border px-3 py-1 text-xs font-semibold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] ${
                      selected
                        ? 'border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]'
                        : 'border-[var(--border)] bg-[var(--surface)] text-[var(--ink-muted)] hover:text-[var(--ink)] hover:border-[var(--ink-subtle)]'
                    }`}
                  >
                    {cat.label}
                  </button>
                );
              })}
            </div>

            {visibleItems.length > 0 && (
              <div className="flex items-center gap-3 shrink-0 text-xs text-[var(--ink-subtle)]">
                <span className="font-mono">
                  {visibleItems.length} {visibleItems.length === 1 ? 'question' : 'questions'}
                </span>
                <button
                  type="button"
                  onClick={toggleAllVisible}
                  className="font-semibold text-[var(--accent)] hover:opacity-85 transition-opacity focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] rounded"
                >
                  {allVisibleOpen ? 'Collapse all' : 'Expand all'}
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Grouped FAQ Sections */}
        {groupedSections.length > 0 ? (
          <div className="space-y-8">
            {groupedSections.map((group) => (
              <section key={group.id} aria-labelledby={`faq-cat-${group.id}`}>
                <div className="mb-3 flex items-center justify-between">
                  <h2
                    id={`faq-cat-${group.id}`}
                    className="text-base sm:text-lg font-bold tracking-tight text-[var(--ink)]"
                  >
                    {group.label}
                  </h2>
                  <span className="font-mono text-[11px] text-[var(--ink-subtle)]">
                    {group.items.length}
                  </span>
                </div>

                <div className="divide-y divide-[var(--border)] rounded-2xl border border-[var(--border)] bg-[var(--surface)] overflow-hidden">
                  {group.items.map((item) => {
                    const isOpen = openIds.has(item.id);
                    return (
                      <div key={item.id}>
                        <button
                          type="button"
                          aria-expanded={isOpen}
                          aria-controls={`faq-panel-${item.id}`}
                          onClick={() => toggleItem(item.id)}
                          className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left text-sm font-semibold text-[var(--ink)] hover:bg-[var(--surface-hover)]/60 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--accent)]"
                        >
                          <span>{item.question}</span>
                          <IconChevron
                            className={`w-4 h-4 shrink-0 text-[var(--ink-muted)] transition-transform duration-200 ${
                              isOpen ? 'rotate-180' : ''
                            }`}
                          />
                        </button>
                        {isOpen && (
                          <div
                            id={`faq-panel-${item.id}`}
                            className="px-5 pb-4 text-sm leading-relaxed text-[var(--ink-muted)]"
                          >
                            {item.answer}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </section>
            ))}
          </div>
        ) : (
          <div className="rounded-2xl border border-dashed border-[var(--border)] bg-[var(--surface)] px-6 py-12 text-center">
            <p className="text-base font-semibold text-[var(--ink)]">
              No matching questions found
            </p>
            <p className="mt-1.5 text-sm text-[var(--ink-muted)]">
              Try a different keyword or reset the topic filter to browse all answers.
            </p>
            <button
              type="button"
              onClick={resetFaqFilters}
              className="mt-4 rounded-xl bg-[var(--accent)] px-4 py-2 text-xs font-semibold text-white hover:opacity-95 transition-opacity focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
            >
              Show all questions
            </button>
          </div>
        )}

        {/* Bottom CTA Returning to Recommender */}
        <div className="mt-12 flex flex-col sm:flex-row sm:items-center justify-between gap-4 rounded-2xl border border-[var(--border)] bg-[var(--surface)] p-6">
          <div>
            <h2 className="text-base font-bold text-[var(--ink)]">
              Ready to find your next read?
            </h2>
            <p className="mt-1 text-xs sm:text-sm text-[var(--ink-muted)]">
              Describe a trope, mood, or add up to 3 favorite titles to get 13 tailored matches.
            </p>
          </div>
          <NavAnchor
            href="/"
            onNavigate={() => onNavigate('home')}
            className="inline-flex items-center justify-center gap-2 self-start sm:self-center shrink-0 rounded-xl bg-[var(--accent)] px-4 py-2.5 text-xs sm:text-sm font-semibold text-white shadow-sm hover:opacity-95 transition-opacity focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            <span>Open Recommender</span>
            <IconArrowRight className="w-4 h-4" />
          </NavAnchor>
        </div>
      </main>
    </>
  );
};

// ============================================================================
// 13. COMPONENT: FOOTER (maps to src/components/Footer.tsx)
// ============================================================================

interface FooterProps {
  page: AppPage;
  onNavigate: (target: AppPage) => void;
}

const Footer: React.FC<FooterProps> = ({ page, onNavigate }) => (
  <footer className="mt-auto border-t border-[var(--border)] bg-[var(--bg)] py-8">
    <div className="mx-auto max-w-7xl px-4 sm:px-6 flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs text-[var(--ink-muted)]">
      <p>
        <strong className="font-semibold text-[var(--ink)]">How it works:</strong> Your request is
        parsed into structured tropes and matched against our catalog—filter changes re-query the
        database instantly without re-running AI.
      </p>
      <div className="flex flex-wrap items-center gap-3 shrink-0">
        <NavAnchor
          href={page === 'faqs' ? '/' : '/faqs'}
          onNavigate={() => onNavigate(page === 'faqs' ? 'home' : 'faqs')}
          className="font-semibold text-[var(--ink)] hover:text-[var(--accent)] transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] rounded"
        >
          {page === 'faqs' ? 'Recommender' : 'FAQs'}
        </NavAnchor>
        <span aria-hidden="true">·</span>
        <span>Adult (18+) content is strictly opt-in.</span>
        {IS_MOCK_MODE && (
          <span className="rounded-md border border-[var(--border)] bg-[var(--bg-subtle)] px-2 py-0.5 font-mono text-[10px] text-[var(--ink-subtle)]">
            MOCK MODE
          </span>
        )}
      </div>
    </div>
  </footer>
);

// ============================================================================
// 14. MAIN APP ORCHESTRATOR (maps to src/App.tsx)
// ============================================================================

export default function App() {
  // Client-side route state ('/' for recommender home, '/faqs' for dedicated FAQ page)
  const [page, setPage] = useState<AppPage>(() => resolvePageFromLocation());

  useEffect(() => {
    const syncRoute = () => setPage(resolvePageFromLocation());
    window.addEventListener('popstate', syncRoute);
    window.addEventListener('hashchange', syncRoute);
    return () => {
      window.removeEventListener('popstate', syncRoute);
      window.removeEventListener('hashchange', syncRoute);
    };
  }, []);

  useEffect(() => {
    document.title =
      page === 'faqs'
        ? 'FAQs — Koma Manga & Manhwa Guide'
        : 'Koma — Manga & Manhwa Guide';
  }, [page]);

  const navigateTo = useCallback((target: AppPage) => {
    const nextPath = target === 'faqs' ? '/faqs' : '/';
    try {
      if (window.location.pathname !== nextPath || window.location.hash !== '') {
        window.history.pushState({ page: target }, '', nextPath);
      }
    } catch {
      // Ignore history access restrictions
    }
    setPage(target);
    window.scrollTo({ top: 0, behavior: 'auto' });
  }, []);

  // Theme state: defaults to dark (for the JustWatch hero look) or saved preference
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    try {
      const saved = window.localStorage.getItem(THEME_STORAGE_KEY);
      if (saved === 'light' || saved === 'dark') return saved;
    } catch {
      // Ignore storage access issues
    }
    return 'dark';
  });

  const toggleTheme = useCallback(() => {
    setTheme((prev) => {
      const next = prev === 'dark' ? 'light' : 'dark';
      try {
        window.localStorage.setItem(THEME_STORAGE_KEY, next);
      } catch {
        // Ignore storage access issues
      }
      return next;
    });
  }, []);

  // Search & filter state
  const [queryText, setQueryText] = useState('');
  const [seedTitles, setSeedTitles] = useState<string[]>([]);
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);

  // Current recommendation payload
  const [response, setResponse] = useState<RecommendResponse | null>(null);
  const [lastAction, setLastAction] = useState<'recommend' | 'refilter' | null>(null);

  // 18+ confirmation modal state
  const [ageModalOpen, setAgeModalOpen] = useState(false);

  // Endpoint hooks (one hook per endpoint)
  const recommendHook = useRecommend();
  const refilterHook = useRefilter();
  const { votes, notesSaved, submitVote, submitNote } = useFeedback();

  // Execute POST /recommend
  const runRecommend = useCallback(
    async (overrideFilters?: Filters) => {
      const activeFilters = overrideFilters ?? filters;
      if (!queryText.trim() && seedTitles.length === 0) return;
      setLastAction('recommend');
      refilterHook.clearError();
      const data = await recommendHook.run({
        text: queryText.trim(),
        filters: activeFilters,
        ...(seedTitles.length > 0 ? { seed_titles: seedTitles } : {}),
      });
      if (data) {
        setResponse(data);
      }
    },
    [queryText, seedTitles, filters, recommendHook, refilterHook]
  );

  // Execute POST /refilter (only when intent_id already exists; never re-sends text)
  const applyFilterChange = useCallback(
    async (patch: Partial<Filters>) => {
      const nextFilters: Filters = { ...filters, ...patch };
      setFilters(nextFilters);

      if (!response?.intent_id) return;

      setLastAction('refilter');
      recommendHook.clearError();
      const data = await refilterHook.run({
        intent_id: response.intent_id,
        filters: nextFilters,
      });
      if (data) {
        setResponse(data);
      }
    },
    [filters, response?.intent_id, refilterHook, recommendHook]
  );

  // Handle 18+ toggle with localStorage age confirmation gate
  const handleToggleMature = useCallback(
    (nextVal: boolean) => {
      if (!nextVal) {
        // Turning off removes mature results via /refilter
        applyFilterChange({ include_mature: false });
        return;
      }

      let confirmed = false;
      try {
        confirmed = window.localStorage.getItem(AGE_STORAGE_KEY) === 'true';
      } catch {
        confirmed = false;
      }

      if (confirmed) {
        applyFilterChange({ include_mature: true });
      } else {
        setAgeModalOpen(true);
      }
    },
    [applyFilterChange]
  );

  const handleConfirmAge = useCallback(() => {
    try {
      window.localStorage.setItem(AGE_STORAGE_KEY, 'true');
    } catch {
      // Ignore storage quota / private mode errors
    }
    setAgeModalOpen(false);
    applyFilterChange({ include_mature: true });
  }, [applyFilterChange]);

  const handleResetFilters = useCallback(() => {
    applyFilterChange(DEFAULT_FILTERS);
  }, [applyFilterChange]);

  const handleRetry = useCallback(() => {
    if (lastAction === 'refilter' && response?.intent_id) {
      refilterHook.run({ intent_id: response.intent_id, filters }).then((data) => {
        if (data) setResponse(data);
      });
    } else {
      runRecommend();
    }
  }, [lastAction, response?.intent_id, filters, refilterHook, runRecommend]);

  const activeError = recommendHook.error || refilterHook.error;
  const hasActiveFilters =
    filters.type !== 'all' ||
    filters.format !== 'any' ||
    filters.readable_only ||
    filters.include_suggestive ||
    filters.include_mature;

  // Accessible status line message
  const statusMessage = recommendHook.loading
    ? 'Understanding your request…'
    : refilterHook.loading
    ? 'Updating results with new filters…'
    : response
    ? `Showing ${response.results.length} recommendations.`
    : '';

  return (
    <div
      data-theme={theme}
      className={`${theme === 'dark' ? 'dark' : ''} min-h-screen flex flex-col bg-[var(--bg)] text-[var(--ink)] antialiased selection:bg-[var(--accent-soft)] selection:text-[var(--accent)]`}
    >
      {/* Explicit Light & Dark Theme Tokens + Modern Typography */}
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@500;600&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');

        [data-theme="light"] {
          --bg: #F8F7F4;
          --bg-subtle: #F0EFEA;
          --bg-glass: rgba(248, 247, 244, 0.88);
          --surface: #FFFFFF;
          --surface-hover: #F3F2EE;
          --border: #E4E2DA;
          --border-subtle: #EEEDE7;
          --ink: #14151A;
          --ink-muted: #575B66;
          --ink-subtle: #828794;
          --accent: #4F46E5;
          --accent-soft: rgba(79, 70, 229, 0.1);
          --accent-ring: rgba(79, 70, 229, 0.22);
          font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        }

        [data-theme="dark"] {
          --bg: #0A0B0F;
          --bg-subtle: #12141B;
          --bg-glass: rgba(10, 11, 15, 0.86);
          --surface: #11131A;
          --surface-hover: #191C26;
          --border: #232734;
          --border-subtle: #1B1E29;
          --ink: #F4F5F8;
          --ink-muted: #9A9FB0;
          --ink-subtle: #666C7E;
          --accent: #6366F1;
          --accent-soft: rgba(99, 102, 241, 0.16);
          --accent-ring: rgba(99, 102, 241, 0.32);
          font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        }

        .font-mono {
          font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
        }
      `}</style>

      {page === 'faqs' ? (
        <FaqPage
          onNavigate={navigateTo}
          theme={theme}
          onToggleTheme={toggleTheme}
        />
      ) : (
        <>
          {/* Hero Header with Angled Blurred Poster Backdrop */}
          <header className="relative overflow-hidden pb-10 sm:pb-14">
            <JustWatchHeroBackdrop isDark={theme === 'dark'} />

            {/* Top Navigation Bar */}
            <TopNav
              page={page}
              onNavigate={navigateTo}
              theme={theme}
              onToggleTheme={toggleTheme}
            />

            {/* Centered Hero Headline + Search Box */}
            <div className="relative z-10 mx-auto w-full max-w-7xl px-4 sm:px-6 pt-4 sm:pt-8">
              <div className="mx-auto max-w-3xl text-center mb-6 sm:mb-8">
                <h1 className="text-3xl sm:text-5xl font-extrabold tracking-tight text-[var(--ink)] leading-[1.12] drop-shadow-sm">
                  Your guide to manga, manhwa &amp; manhua
                </h1>
                <p className="mt-3 text-sm sm:text-base text-[var(--ink-muted)] max-w-xl mx-auto">
                  Describe a trope, mood, or plot twist and get 13 curated titles—then filter instantly.
                </p>
              </div>

              <div className="mx-auto max-w-3xl">
                <SearchBox
                  text={queryText}
                  onTextChange={setQueryText}
                  seedTitles={seedTitles}
                  onSeedTitlesChange={setSeedTitles}
                  onSubmit={() => runRecommend()}
                  loading={recommendHook.loading}
                />
              </div>
            </div>
          </header>

          {/* Main Content */}
          <main className="mx-auto w-full max-w-7xl flex-1 px-4 sm:px-6 pb-8">
            <FilterBar
              filters={filters}
              onChangeFilter={applyFilterChange}
              onToggleMature={handleToggleMature}
              refiltering={refilterHook.loading}
            />

            <div className="mt-5 space-y-5">
              {/* Accessible Live Status Line */}
              <div aria-live="polite" className="min-h-[20px]">
                {statusMessage && (
                  <p className="text-xs font-medium text-[var(--ink-muted)]">
                    {statusMessage}
                  </p>
                )}
              </div>

              {/* Errors, Degraded Mode, Not-Found & Skipped Notices */}
              <NoticeBanners
                error={activeError}
                onRetry={handleRetry}
                degraded={response?.degraded ?? false}
                notFoundTitles={response?.not_found_titles ?? []}
                skippedTitles={response?.skipped_titles ?? []}
              />

              {/* Loading Skeletons / Results Grid / Empty State / Idle Prompts */}
              {recommendHook.loading ? (
                <SkeletonGrid />
              ) : response ? (
                <>
                  <IntentChips
                    intent={response.intent}
                    latency={response.latency_ms}
                    total={response.total}
                  />

                  {response.results.length > 0 ? (
                    <section
                      aria-label="Recommended titles"
                      className={`grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-5 transition-opacity duration-200 ${
                        refilterHook.loading ? 'opacity-60' : 'opacity-100'
                      }`}
                    >
                      {response.results.map((series) => (
                        <SeriesCard
                          key={series.id}
                          series={series}
                          vote={votes[series.id]}
                          savedNote={notesSaved[series.id]}
                          onVote={(seriesId, vote) =>
                            submitVote(response.intent_id, seriesId, vote)
                          }
                          onSaveNote={(seriesId, note) =>
                            submitNote(response.intent_id, seriesId, note)
                          }
                        />
                      ))}
                    </section>
                  ) : (
                    <EmptyState
                      hasActiveFilters={hasActiveFilters}
                      onResetFilters={handleResetFilters}
                    />
                  )}
                </>
              ) : (
                /* Clean Initial Idle Prompt Suggestions */
                <section
                  aria-label="Example prompts"
                  className="mx-auto max-w-3xl pt-6 text-center"
                >
                  <p className="text-xs font-semibold uppercase tracking-wider text-[var(--ink-subtle)]">
                    Try a request
                  </p>
                  <div className="mt-3 flex flex-wrap items-center justify-center gap-2">
                    {EXAMPLE_PLACEHOLDERS.map((example) => (
                      <button
                        key={example}
                        type="button"
                        onClick={() => {
                          setQueryText(example);
                        }}
                        className="rounded-full border border-[var(--border)] bg-[var(--surface)] px-3.5 py-1.5 text-xs text-[var(--ink-muted)] hover:border-[var(--accent)] hover:text-[var(--ink)] transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
                      >
                        &ldquo;{example}&rdquo;
                      </button>
                    ))}
                  </div>
                </section>
              )}
            </div>
          </main>
        </>
      )}

      {/* Footer */}
      <Footer page={page} onNavigate={navigateTo} />

      {/* 18+ Confirmation Modal */}
      <AgeGateModal
        open={ageModalOpen}
        onConfirm={handleConfirmAge}
        onCancel={() => setAgeModalOpen(false)}
      />
    </div>
  );
}