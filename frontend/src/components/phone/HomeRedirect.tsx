import { Navigate } from 'react-router-dom';

import { usePhoneShell } from '@/lib/phoneShell';
import { ROUTES } from '@/lib/routes';

/* `/` is where each shell starts: the feed on the phone shell (the installed
 * app's start screen, #1766), Browse on the desktop one. */
export default function HomeRedirect() {
  const phone = usePhoneShell();
  return <Navigate to={phone ? ROUTES.feed.build() : ROUTES.browse.build()} replace />;
}
