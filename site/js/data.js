// 카드 데이터 읽기. 실제 데이터(data/cards.json, #17)가 아직 없으면 예시(data/cards.sample.json)를 쓴다.

const REAL_DATA_URL = 'data/cards.json';
const SAMPLE_DATA_URL = 'data/cards.sample.json';

/**
 * @param {typeof fetch} [fetchJson]  테스트에서 바꿔 끼우는 fetch
 * @returns {Promise<{data: unknown, source: 'real'|'sample'}>}
 * @throws {Error} 실제 데이터가 404가 아닌 이유로 실패하거나, 예시도 읽지 못하면
 */
export async function loadCardData(fetchJson = fetch) {
  const real = await fetchJson(REAL_DATA_URL, { cache: 'no-cache' });
  if (real.ok) return { data: await real.json(), source: 'real' };
  // 404만 "아직 없음"으로 본다. 그 밖의 실패를 예시로 덮으면 배포 오류를 놓친다.
  if (real.status !== 404) throw new Error(`${REAL_DATA_URL} 읽기 실패: HTTP ${real.status}`);

  const sample = await fetchJson(SAMPLE_DATA_URL, { cache: 'no-cache' });
  if (!sample.ok) throw new Error(`${SAMPLE_DATA_URL} 읽기 실패: HTTP ${sample.status}`);
  return { data: await sample.json(), source: 'sample' };
}
