-- 659_ceskereality_chaty_chalupy_scope_gone.sql -- ceskereality walks no `chaty-chalupy` scope (migration 249
-- added two): the `rodinne-domy` walk already descends into chaty + chalupy and no listing ever carried a
-- `/chaty-chalupy/` URL, so the scope only counted ~2,000 houses twice in dum/prodej's declared total (#1854).

do $$
declare
  v_before jsonb := (select categories from public.portals where source = 'ceskereality');
  v_kept jsonb := (select jsonb_agg(e order by i) from jsonb_array_elements(v_before) with ordinality as t(e, i)
                    where e ->> 'category' is distinct from 'chaty-chalupy');
begin
  update public.portals set categories = v_kept where source = 'ceskereality' and categories <> v_kept;
  if (select categories from public.portals where source = 'ceskereality') is distinct from v_kept
     or jsonb_array_length(v_before) - jsonb_array_length(v_kept) not in (0, 2) then
    raise exception '659 did not land: ceskereality categories are not 249''s less its two chaty-chalupy scopes';
  end if;
end
$$;
