/* Czech words for what Supabase Auth refuses. Its own messages are English, so a code maps here and anything unknown
 * reads as a plain failure; the invitation refusal is already Czech (migration 716's hook) and passes through. */

const REFUSED = 'Tato adresa zatím nemá pozvánku. Přidejte se na čekací listinu na propscout.cz.';
const FAILED = 'Nepovedlo se to. Zkuste to prosím znovu.';

const BY_CODE: Record<string, string> = {
  invalid_credentials: 'Nesprávný e-mail nebo heslo.',
  signup_disabled: REFUSED,
  user_already_exists: 'Tato adresa už účet má. Přihlaste se.',
  email_exists: 'Tato adresa už účet má. Přihlaste se.',
  weak_password: 'Heslo je příliš slabé. Použijte aspoň 8 znaků.',
  same_password: 'Nové heslo musí být jiné než to současné.',
  over_email_send_rate_limit: 'Poslali jsme toho teď příliš. Zkuste to prosím za chvíli.',
  over_request_rate_limit: 'Příliš mnoho pokusů. Zkuste to prosím za chvíli.',
};

export function authErrorCs(err: unknown): string {
  const { code, message } = (err ?? {}) as { code?: unknown; message?: unknown };
  if (typeof message === 'string' && message.includes('pozvánku')) return message;
  return (typeof code === 'string' && BY_CODE[code]) || FAILED;
}

/* A Google sign-in the hook refused comes back as ?error_description=… or #error_description=… on the address it was
 * sent to, which RequireAuth then stashes for this screen. */
export function redirectError(...parts: (string | undefined)[]): string | null {
  for (const part of parts) {
    if (!part) continue;
    const params = new URLSearchParams(part.replace(/^[?#]/, ''));
    if (params.has('error') || params.has('error_description')) {
      return authErrorCs({ code: params.get('error_code'), message: params.get('error_description') });
    }
  }
  return null;
}
