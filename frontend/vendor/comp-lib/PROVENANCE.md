# Vendored Swiss 1 foundations — provenance

This is a **pinned copy** of the design library, vendored so the product builds with no external path, no
publish step and no network. It is the product's UI authority under
[ADR-0026](../../../docs/adr/0026-the-luvn-component-library-is-the-ui-authority.md), which supersedes the
vendor choice of [ADR-0016](../../../docs/adr/0016-swiss-1-foundations-is-the-ui-authority.md) and retains its contract.

## Where it came from

| | |
|---|---|
| Repository | **`ECDX-Platforms/LUVN-Component-Library`** (private; pull-only for this product) |
| Revision | **`014b02a`** — *"Redesign v2.3 part 3, on the current main"*. Pinned to this commit, never `main`. |
| Package | `@luvn/component-library` 0.1.0, `private: true` |
| Copied | 2026-10-14, from a clean clone at `014b02a` |
| Figma | `fileKey P8E4RKubznUkk8vi9765oW`, **Design-Library**, the same file the styleguide v2.1 mirrors |
| Runtime dependency | `lucide-react` and React ≥19, as before. The library's `@fontsource/host-grotesk` dependency is **not** adopted: the product self-hosts Host Grotesk (`frontend/src/fonts.css`). |
| Previous source | `ECDX-Platforms/comp-lib-dev` @ `main` `164683d`. That repository describes itself as the rehearsal: *"At the event the real library is `LUVN-Component-Library`"* (`docs/EVENT-RUNBOOK.md`, the copy this tree carried before this re-pin). |

Every vendored file is copied from `LUVN-Component-Library` at the revision above. Nothing is carried from the rehearsal repository.

## What is here

The token layer (`tokens/`, `src/styles/`) and the components. **227 files** are pinned: 69 component directories and 215 files under `src/components`, the root barrel, the token layer, the base and index styles and the Figma exports.

Those counts are the ones the gate enforces: `design-system.test.mjs` sweeps `src/`, `tokens/` and `src/styles/`
and fails if a file is missing from the table below or if the table names a file that is not there, so the table
and the tree cannot disagree.

- **The barrel is the entry point, and the per-component `index.ts` files are required.** `src/index.ts`
  re-exports the components by **directory**, so each `<Name>/index.ts` is what resolves; the product imports
  `../vendor/comp-lib/src/index.ts`. A vendored copy without them fails to load.
- **Styles** enter through `src/styles/index.css`, which imports `tokens.css` then `base.css` in that order.
  `frontend/src/design-system.css` is the single import site.
- **Font.** `--font-sans` is `"Host Grotesk", system-ui, sans-serif`. The product loads Host Grotesk 400, 500
  and 600 in `frontend/src/fonts.css` from `frontend/public/fonts/`. The gate asserts both halves: the token value,
  and that `fonts.css` declares the same family at those weights. Without the second half, a token change
  would fall back silently to `system-ui`.

## Never hand-edit the generated files

`tokens/dtcg/*.json` and `src/styles/tokens.css` are **generated** from `tokens/figma-export.json` and
`tokens/styleguide-only.json`. Editing them here creates a copy that silently disagrees with Figma — the
failure mode this gate exists to prevent.

```bash
# from a clean clone of ECDX-Platforms/LUVN-Component-Library at 014b02a (never main)
npm install          # style-dictionary only; needs network, once
npm run tokens       # figma-to-dtcg -> build-tokens -> verify-tokens (206 checks against the Figma export)
```

That is a **manual, deliberate step**, never part of the product's gate. Re-vendoring then means: copy the
files from a clean clone at the pinned revision, regenerate the checksum table below (every row is a
`sha256sum` of the vendored file, not a typed value), update the revision above, and run
`scripts/check.sh --full`. Never copy a file and hand-patch it.

## Deliberately not vendored

Per ADR-0026, the exclusion boundary is the one this ledger applied to the rehearsal copy, extended to the new
repository's own additions: `*.stories.tsx`, `*.test.tsx`, `*.test.ts`, `*.check.ts` and `*.figma.ts` (they import Storybook, Vitest,
Playwright or `@figma/code-connect`, which the product does not install), `src/foundations/` (Storybook pages
only), `src/styles/fonts.css` (the product self-hosts its font), `src/styles/` files other than the three above,
`.storybook/`, `test/`, `scripts/`, `.github/`, `tsconfig.json`, `vite.config.ts`, `vitest.config.ts`,
`figma.config.json`, `package.json`, `package-lock.json`, and the library's devDependencies. `docs/` is not
vendored except `EVENT-RUNBOOK.md`.

## Pinned checksums

`frontend/src/design-system.test.mjs` re-hashes every vendored file listed here and fails on a single changed
byte. It also asserts **completeness** — every file under `src/`, `tokens/` and `src/styles/` that the product
builds from must appear in this table — so a component cannot be added without being pinned.

### The table carries each file's source

`Source` is the revision the file was copied from. Every row carries `014b02a`; the gate asserts it.

| Path | SHA-256 | Source |
|---|---|---|
| `src/components/AiSummaryBlock/AiSummaryBlock.module.css` | `067a5820ca951363082280e64c912e18a02c50d591d9f80b47e5dc70fde84cba` | `014b02a` |
| `src/components/AiSummaryBlock/AiSummaryBlock.tsx` | `4fee778517525749c65b03a0cb47c69723a098cf1e3bb6887cb322d408523231` | `014b02a` |
| `src/components/AiSummaryBlock/index.ts` | `f8af4816823c0f0936ed092fde2b49a2d1d134d9540db2161a26229184ca81b8` | `014b02a` |
| `src/components/Alert/Alert.module.css` | `67b7dd8101861522cb79e79cd1e00a94ae6942eff5d0661396f9b3f16393c3b4` | `014b02a` |
| `src/components/Alert/Alert.tsx` | `31669054495148c458adffb81d672fd25fef1930fd372d3d64b68bf78a76110a` | `014b02a` |
| `src/components/Alert/index.ts` | `3c27255caa33ccc5dbcd0976187fb06f982475fdae76eb1d0548b2be93f4df5f` | `014b02a` |
| `src/components/AppShell/AppShell.module.css` | `9b75d6eee822cbbd3511a8adfff3c57300a86373781fb203fed078ea3bf993df` | `014b02a` |
| `src/components/AppShell/AppShell.tsx` | `019390a12f0533573713b7ada51f37f70ea49fd73f04d003666010276130bc5a` | `014b02a` |
| `src/components/AppShell/index.ts` | `6a962d25a987402bd992817212a14f724df9f14730e3fad24d764d07161d839c` | `014b02a` |
| `src/components/AppShell/storyShell.tsx` | `6768d8f89c16536ae173f18e3ca28d5fe15796c59ec1bed33899fef124c13b3a` | `014b02a` |
| `src/components/Avatar/Avatar.module.css` | `619764c3adb925216197152c4159f832ba1c72c0b170dd98e4e6f4fa4935c781` | `014b02a` |
| `src/components/Avatar/Avatar.tsx` | `5e95ef8c43962b3c35358a9cd47d83ba547a358e9424a87b47dbd2b72818dfd0` | `014b02a` |
| `src/components/Avatar/index.ts` | `b426fa679600c74dbdb157aa3f0f59b35f1d693c8a064ca465d57ff62df1aca8` | `014b02a` |
| `src/components/Block/Block.module.css` | `fcdc5f8eb025dafa030029e5df6f84bb23164b0cd8c43cbb5889c26137d28e9b` | `014b02a` |
| `src/components/Block/Block.tsx` | `41f3eecc034e2167e9639e2d752db6f20413fae3084d769985b85f2987bc466e` | `014b02a` |
| `src/components/Block/index.ts` | `64a4af2ce1bb0961fdf21ff4b77b4a09fd1ea7e3b4f5340d843272f770308f75` | `014b02a` |
| `src/components/BlockGrid/BlockGrid.module.css` | `6f217d7c38d13dfb1d945bcf098905b58e8a12da6c927893d07a4112da7c8f57` | `014b02a` |
| `src/components/BlockGrid/BlockGrid.tsx` | `49d6974c3c82d17232a037e12aa124956fcd618a15e84bd8f29af0af4be2a8eb` | `014b02a` |
| `src/components/BlockGrid/index.ts` | `d73a13af49ba1541b009acdbc7cf2d21d1c50751d4a89b5606bbe93ffc283233` | `014b02a` |
| `src/components/BlockState/BlockState.module.css` | `7883e3206a369f3de5fae407c6e7d1aa918746c0e3e2e7d7db372a50df2f9c4b` | `014b02a` |
| `src/components/BlockState/BlockState.tsx` | `a9a9a6f3504b0e28df496406642cc49e56ed0e89f458c95002f0427556e6dce2` | `014b02a` |
| `src/components/BlockState/index.ts` | `6390c952a52c0050876c6ed8aac50ce9faa153f8172a3fe89d31714895df06fc` | `014b02a` |
| `src/components/BottomNav/BottomNav.module.css` | `07008a2bee9db1f745de6bc9a03364c18ebc2ef1f61f49d80ea3ae68bfb88abe` | `014b02a` |
| `src/components/BottomNav/BottomNav.tsx` | `15fb49c7c76e033f9cf81f93add8cab4f7a74309dd3b7e1bb33ac46c504640b6` | `014b02a` |
| `src/components/BottomNav/index.ts` | `5f9d8b637f9a68779f1d107fd5eb175a2420be0f63835891184e7019228f6bbc` | `014b02a` |
| `src/components/Breadcrumb/Breadcrumb.module.css` | `d0b687302e03823d61736ca510ce9b94707397bb37e643640ed9ffd3ed220294` | `014b02a` |
| `src/components/Breadcrumb/Breadcrumb.tsx` | `ed59865caf190d4083cd212caa42ee98dfb6c6a6d36d67c3bc41f0dc3933b83d` | `014b02a` |
| `src/components/Breadcrumb/index.ts` | `04fbb2c395c34f73573e5f32a7cc6ff7f4e7ba92b5ebe44f1c6c630c55144707` | `014b02a` |
| `src/components/Button/Button.module.css` | `a5c1754074ff3540e36b711e5875a0dea643b5dce2b583dde8eca8852d8a2bad` | `014b02a` |
| `src/components/Button/Button.tsx` | `45af8574d7800347c7fb4e8153771c557136247518f83fbdbb0b3c1db4b29e76` | `014b02a` |
| `src/components/Button/index.ts` | `bf0fa7f82ae16ba5897a1b589fbf8604e9a57a8ec1c7cc522ebbfb89e93f578c` | `014b02a` |
| `src/components/CareOverview/CareOverview.module.css` | `ef84e05dd201806d4e83af8eb88f11c5ebf8b1472ad2cbe0cf8246973d9fcac2` | `014b02a` |
| `src/components/CareOverview/CareOverview.tsx` | `98ac63162ea8afd69db0a0ba45a40d18a8d2a38cc960f06253d4c7cfb8ff5c7d` | `014b02a` |
| `src/components/CareOverview/index.ts` | `63c6b8726323855b74529c1974c6028f40d314e12ccad876511df89d3464ceca` | `014b02a` |
| `src/components/CareOverview/overview.ts` | `23da71e9dcee41baa1a8bdfd523f47f4fffe8e1917d056b0d6a7951243f80577` | `014b02a` |
| `src/components/CautionItem/CautionItem.module.css` | `6bf912175c7c8001dbb44a223bd5b1b2d43596c120ae3924edbb195c18e08423` | `014b02a` |
| `src/components/CautionItem/CautionItem.tsx` | `26eb6598c38649690b2b70559b700f6c996cc7f91cd9655bb17db634925e3c2a` | `014b02a` |
| `src/components/CautionItem/index.ts` | `aca3b20ab14acbf269100d4440a5f00334e99648d83fca89b7766d64ff038bc8` | `014b02a` |
| `src/components/Checkbox/Checkbox.module.css` | `ff0b62cda2899d5194fdcb9b531f3dc41787b1c1f01f4dc4ce3db45d9b21f08a` | `014b02a` |
| `src/components/Checkbox/Checkbox.tsx` | `4ccb2c4cd83a7abb3eebeb4beb657ed10a46bef3624800c3367c9205ed26efbb` | `014b02a` |
| `src/components/Checkbox/index.ts` | `0855fd4f7dd71acbfc406968d94252865790922b2c9700c6abb241479daf0ac2` | `014b02a` |
| `src/components/ClientTimeline/ClientTimeline.module.css` | `1d9bdd45db7271d0b9f7688c6cc226787a33d3ab2614c06d8051da8c87cefacc` | `014b02a` |
| `src/components/ClientTimeline/ClientTimeline.tsx` | `63f1f86583b3689fadc3b22a27c5d541936bfad08f4b65bb48367fe766c2d021` | `014b02a` |
| `src/components/ClientTimeline/index.ts` | `8a583c6c76a37a0b67d72a014c1a04f0b3e387cc3538bc2857b00613e30205b1` | `014b02a` |
| `src/components/CountBadge/CountBadge.module.css` | `0cff46e86530181320f635e04129a3a2e8a3cdb3d4b5614d343196c6344133f4` | `014b02a` |
| `src/components/CountBadge/CountBadge.tsx` | `4463ec92c22e0cba7ebadab008c3ed8d76de3f576fc15f5ce32b00867f43233a` | `014b02a` |
| `src/components/CountBadge/index.ts` | `37a8ce667cc517b3509d7846b1f0a7f67175f20d0d9670e789a88831afc5c1cc` | `014b02a` |
| `src/components/Dialog/Dialog.module.css` | `ac26a1c9e6f8f3f772060152675dd4c19406f70cc1a95f2ad2ce9065b5e30e92` | `014b02a` |
| `src/components/Dialog/Dialog.tsx` | `0a8dbfeeab9516e4c2de162fad7fc168aae10a75d3703d1c2438b0db326b19cd` | `014b02a` |
| `src/components/Dialog/index.ts` | `244e50156a304786f3906063e13b9a38f4d5dd787985732d115852a396a84e65` | `014b02a` |
| `src/components/Divider/Divider.module.css` | `1c050e12b06006eb4421f028556d124e5cb21890b51a4ed97224afaebd7c5cc3` | `014b02a` |
| `src/components/Divider/Divider.tsx` | `16354e02f0aec13887d950db34939e50cd5ab1d50690ad495963dbb35d1f9200` | `014b02a` |
| `src/components/Divider/index.ts` | `5b69306560ecdb3b5d8ba7d47a0ba221814c7fa3a085aa53436aa44e192ade03` | `014b02a` |
| `src/components/Field/Field.module.css` | `e4a1d13bd62565c068fbde71acd1920b054ad22819d16c0a45a53dfe3a7893ba` | `014b02a` |
| `src/components/Field/Field.tsx` | `0664ad5574287692404125ef8ab678dca1317612a403fe5612b4a3da1fb7671e` | `014b02a` |
| `src/components/Field/index.ts` | `d32d7d0f0fc44199972e61da4011396633ba298e2f80b31ed1b0e0c7362c6181` | `014b02a` |
| `src/components/Flag/Flag.module.css` | `af62546c5105dc8aabf6f22fe460771e692898cba7da2ea8032bddeeec0f55ce` | `014b02a` |
| `src/components/Flag/Flag.tsx` | `40d44b5021a6457b62b40e854e34236e91b953da248f3fb67349f17814c3ffeb` | `014b02a` |
| `src/components/Flag/index.ts` | `1c793a4f29e50913941abcaaa542fa2da0fe08bbaf7b1dca654c2ac5162f26c9` | `014b02a` |
| `src/components/GroupedRecordRow/GroupedRecordRow.module.css` | `493b33a382da8712aa8fac8f84a3217907f762d7cbfbc480bfe083c29d427db1` | `014b02a` |
| `src/components/GroupedRecordRow/GroupedRecordRow.tsx` | `29fa1551af01d0597c75c22764f2d9245d467d51eb65dd0bff6beadee5f45e3a` | `014b02a` |
| `src/components/GroupedRecordRow/index.ts` | `c817172e4c386c05668ce3b8f7a15f6f8b3aa7d4ece1824191cd91a81e98ee18` | `014b02a` |
| `src/components/Header/Header.module.css` | `0093d52daed0d4842b0cd3ea88605d9d1a8bbb5747ffb5c8c2763d143c785a90` | `014b02a` |
| `src/components/Header/Header.tsx` | `2d8733159c16e9f92ddcd03041a4957990f6cbeadafd1c48012bd7757341eccf` | `014b02a` |
| `src/components/Header/index.ts` | `5d09b63997a865829643f320ec54aa337af1e6917e004d77514d13788546e7bc` | `014b02a` |
| `src/components/IconButton/IconButton.module.css` | `20209b0c97c559c6bd27be2d4bdca36c3f72584042bb11ce19448595d384cebe` | `014b02a` |
| `src/components/IconButton/IconButton.tsx` | `55f140506fcce687e8f0e88a05e4c3c76b4aa4bd91f1ce05dd5bf4c097ccb206` | `014b02a` |
| `src/components/IconButton/index.ts` | `7194486598781ec8014a2337c3bbb42178655350e3e744fd53d86cdf9b77a823` | `014b02a` |
| `src/components/IdentityStrip/IdentityStrip.module.css` | `53d7789028e78dc57716797cef9e90992f26ddc6c116d99b2af087fc70f1e06f` | `014b02a` |
| `src/components/IdentityStrip/IdentityStrip.tsx` | `afca5d73d6bf55a80b2e020a0963363b1b34cf5937752ff393ea748ed3747944` | `014b02a` |
| `src/components/IdentityStrip/index.ts` | `39375f49996d748287b21b26adf08601aa076378cba22c014b74a5bb8968b42c` | `014b02a` |
| `src/components/Input/Input.module.css` | `017abaa0cad9fe6e80bc7a9db5aaa7466d34acbb58397896833380a977a1028b` | `014b02a` |
| `src/components/Input/Input.tsx` | `afba864dda7071ab97b85397027b8699b8b2b9d2310249b15cf79e27da229227` | `014b02a` |
| `src/components/Input/index.ts` | `7a5da26a636db914e9d058845d23927ab943b9ae7ec1886a49d778c703181389` | `014b02a` |
| `src/components/KeyHint/KeyHint.module.css` | `28a54dae28f7f44e811b015791b22a4778a0596def5d5c828481d1cc77bea5c2` | `014b02a` |
| `src/components/KeyHint/KeyHint.tsx` | `5c740b0c5ef6699ff2ade9e9e4efadc683d062326d2b3568983bf7ea5c375768` | `014b02a` |
| `src/components/KeyHint/index.ts` | `4264692a169f5e239e4c9425e4e5c9595dd27d2679a293239d5f4a5054a1302b` | `014b02a` |
| `src/components/KeyValueList/KeyValueList.module.css` | `19fab75f7ff015a500e26b98ad640e27fe39f2bfe29d2fac65ee619e09dcfa6f` | `014b02a` |
| `src/components/KeyValueList/KeyValueList.tsx` | `1943072957a2fc542712eb6a0585051c34932bafde70a167b261ea084abea05d` | `014b02a` |
| `src/components/KeyValueList/index.ts` | `ccf6f627668e53eb564493167853375ef9cc7e1a445f62090bb37f9a775ec3f1` | `014b02a` |
| `src/components/Link/Link.module.css` | `b002846d36ac2b91f7c355ecfbec082f9b7033292385fe07cfafaa615355780e` | `014b02a` |
| `src/components/Link/Link.tsx` | `2b87411fabc58f5f84eedaec6d1b558251c86f78bb74b9157cd337201fb21bad` | `014b02a` |
| `src/components/Link/index.ts` | `b9b71e9e4de9ddd48dba01bc8c408772fdfeadf044361caaa9ebedac974c125b` | `014b02a` |
| `src/components/MainSide/MainSide.module.css` | `bdbd328cdd29927e50eed289061c9bb6e001abd3db8f291d262c4d48e3a28d92` | `014b02a` |
| `src/components/MainSide/MainSide.tsx` | `cedcc88f7bc6d0a7198ab2b718d7138874096ce19cdf7f6756fad16bcf1dd4d0` | `014b02a` |
| `src/components/MainSide/index.ts` | `50f38df1180d213e0aea3a997c1c5ce5baf23064884d0c6f94fb4a4678cedbdd` | `014b02a` |
| `src/components/Menu/Menu.module.css` | `80f4f7e34239e408141fa360bdc3c47455b98772558471addd68bc857013de70` | `014b02a` |
| `src/components/Menu/Menu.tsx` | `eb932a6724d441b790090cbfa7af6abf3e82a8a2a399bd9da5766480c68682e4` | `014b02a` |
| `src/components/Menu/index.ts` | `2b0514a07b2caae0a5e15d06b23e2671a58eaf7326d0e98655cad1d4479f6bdd` | `014b02a` |
| `src/components/Metric/Metric.module.css` | `debf88d10ff42ea4ac7299233fa16a3f88053f7302ff5f44f00bd8478237e9c5` | `014b02a` |
| `src/components/Metric/Metric.tsx` | `da06c8523a1adb9e6eee9fbe845ff4928409d207818edff01bb60debfa4b3973` | `014b02a` |
| `src/components/Metric/index.ts` | `06ea9b16d2fed833c385c063b6c6f3c227599273da8de0e0555f1c0faebd9711` | `014b02a` |
| `src/components/MetricRow/MetricRow.module.css` | `ae5b92c00c9a960e37d47e626764dbd8bcab6e031f42ce02992ef66f8de29488` | `014b02a` |
| `src/components/MetricRow/MetricRow.tsx` | `10cf2ce03a7ed1ed7c5ef84d4041305e9288981cb4de1b8a590ec39c9d362ddb` | `014b02a` |
| `src/components/MetricRow/index.ts` | `bec5ce0cc445dccbda7aaa3dffd15f76b0258da5c6f1f02d5ac9101bb82d0154` | `014b02a` |
| `src/components/NavItem/NavItem.module.css` | `d2c11afd2689d8246d83ecd604cffe68cce180a2bedfc290c250624fc041b0b7` | `014b02a` |
| `src/components/NavItem/NavItem.tsx` | `f96155ebacdfd8d49f8f1f157b1316971ac262834b3a722d55cff72eef77dc35` | `014b02a` |
| `src/components/NavItem/index.ts` | `720be54e652042d403df1bec818ed39e12a4391b40def6966567274b1dda67b9` | `014b02a` |
| `src/components/NotOnRecordLine/NotOnRecordLine.module.css` | `058cfd901bfae6f3b14e768de6f433d6b337c8ffee03d53a54a4d21810b8922b` | `014b02a` |
| `src/components/NotOnRecordLine/NotOnRecordLine.tsx` | `1b441c0987d87e6b028b73de860e4bf0e2aaccb3f6446d8d555e24d9aed38fd9` | `014b02a` |
| `src/components/NotOnRecordLine/index.ts` | `786dfc2fba2ff361ddb0df37a0136948a25beab6f20783ea6b422b73fabf67f7` | `014b02a` |
| `src/components/NowCell/NowCell.module.css` | `81e3c03b697f0dafc0ee5d953fdfa150bf65a5170af44155ea5790e3640922e6` | `014b02a` |
| `src/components/NowCell/NowCell.tsx` | `7951127f2bf0ddb93f231701c3b185f15860c8067a2699a8cb7bc1e67939686c` | `014b02a` |
| `src/components/NowCell/index.ts` | `e62653294b600594c9d435017fde6556e887863ad7494d964b78e46499b3c4cc` | `014b02a` |
| `src/components/NowStrip/NowStrip.module.css` | `6644b1d50f3259252b188e872939ce24f3ac1c431456fa94f767b8f2945b4a8d` | `014b02a` |
| `src/components/NowStrip/NowStrip.tsx` | `ebc54d0cbd6e769294101ce8aad691b98a44f7a0bde3f65823e50edd5de0e976` | `014b02a` |
| `src/components/NowStrip/index.ts` | `aa5cc5131e12c39245e9e31a023679d855603de64f5ad2000c09b85907484b7a` | `014b02a` |
| `src/components/OneColumn/OneColumn.module.css` | `f37d1148fa88a5743e2671282bd0ebfa50e52d6b74798c117d74cce26a85aef4` | `014b02a` |
| `src/components/OneColumn/OneColumn.tsx` | `9a3e8ead2050ae700d3310cf67903beda410f790517d7714b475b71309a91516` | `014b02a` |
| `src/components/OneColumn/index.ts` | `d7354a696fd1d8edb390a32f389291d7b7d826f0540884c0b664bcfc1d6c8b7b` | `014b02a` |
| `src/components/Pagination/Pagination.module.css` | `f42ab05aeb365cba20c0c1dacd2aa12be5d9adbb9dd9b9c6521248e0f06177d6` | `014b02a` |
| `src/components/Pagination/Pagination.tsx` | `9796200a6191f8a99aeb0536c10d806013ddd5be590c8ba211861846c7d6173d` | `014b02a` |
| `src/components/Pagination/index.ts` | `0f19e23f4ab1a73814a584f9917e4f1184801e6b70d1fcad004515cb73db1fb6` | `014b02a` |
| `src/components/ProcessSteps/ProcessSteps.module.css` | `44c272d02fc1ddbd8d30e1d7c72c27d10eff226b617f297e1fa7009fea5188e0` | `014b02a` |
| `src/components/ProcessSteps/ProcessSteps.tsx` | `6439e5cafcd555343e436b9686065e28c443fa5cae9d2452297dcd331828ed12` | `014b02a` |
| `src/components/ProcessSteps/index.ts` | `dff12391158a322e57017fa0cce93cc9b6763aeed60f115c88aa4513f7d4057e` | `014b02a` |
| `src/components/Radio/Radio.module.css` | `917f44bcf22c2090b32d9c31c0856b83213a3368bd6384f1e531d413472c2878` | `014b02a` |
| `src/components/Radio/Radio.tsx` | `90740ee42a52c158543dd3a8c557717b652be3ed6fd7861d90c9a7b0b204b405` | `014b02a` |
| `src/components/Radio/index.ts` | `d4a102504c735937cb2f0390f4f66422f381f0661946e51705d4f8a030b7bbe5` | `014b02a` |
| `src/components/RecordBlock/RecordBlock.module.css` | `11273dfa69b7916f9badb9fa0cad0fafbf171a9150d9d561887333f23ad408ee` | `014b02a` |
| `src/components/RecordBlock/RecordBlock.tsx` | `10beba51a1cee08ba3def1fd075b3d8b0be8eded0924723f5466a996d18d4b30` | `014b02a` |
| `src/components/RecordBlock/index.ts` | `a8b6455b4d3d915b42b0f87582c9d314355874404e3959981828d169ebafb864` | `014b02a` |
| `src/components/ResultSeries/ResultSeries.module.css` | `371ea161744a766ffa1376787097c2b1b1d1d6d32c9338176dce523d0b58e412` | `014b02a` |
| `src/components/ResultSeries/ResultSeries.tsx` | `08781f7cb1b5db4556a75c010b25e8ee3f016cf2abde17bdc9cf6d74cade62a4` | `014b02a` |
| `src/components/ResultSeries/axis.ts` | `b18b600ae07c960961f585d315187aad52dedc17337a5a5b9b2a7ab954191634` | `014b02a` |
| `src/components/ResultSeries/dates.ts` | `77a7c7ee9cc2dbd15ed006d5fbf9c867a9e8e7f22a800fdb112b4d16c9d3eeef` | `014b02a` |
| `src/components/ResultSeries/index.ts` | `4897ed7e98bc98b463096c3a361b5f6cd7f0df2b261166fe9aaeef4c56e69db6` | `014b02a` |
| `src/components/ResultSeries/scales.ts` | `2b97a9a4f470a96cbf4e5d8a75e43c1ec30a1d2fc8f6df15d6e050c84bd89246` | `014b02a` |
| `src/components/SameFactTwoSources/SameFactTwoSources.module.css` | `be4010509e63b73abbf6273ed0b5c178d1494723500eec2870fdf6449b02eb6a` | `014b02a` |
| `src/components/SameFactTwoSources/SameFactTwoSources.tsx` | `692a90efe9bc82731426065dcdf47e1a52351d0bc214869ede3d445ca974a29d` | `014b02a` |
| `src/components/SameFactTwoSources/index.ts` | `07093f2b07ba4c470f18cf830c9eb7be85610e43b6094937deab47dbeaa277f2` | `014b02a` |
| `src/components/Search/Search.module.css` | `d8d7756f764b777335c29afa99ae44cdbd317268f93886c72177bc9dbaee16fd` | `014b02a` |
| `src/components/Search/Search.tsx` | `5e200d9084a9626939b93b52cfac80b4fa82d3d893b15b6dac5d31bfd6362b8e` | `014b02a` |
| `src/components/Search/index.ts` | `f5c1247bf61ae21bf8076d1b9b980f7e462fa23150c0f8fd66477c029bbc1889` | `014b02a` |
| `src/components/SegmentedControl/SegmentedControl.module.css` | `f2a9de0f08ed9a464748c9e02b32868478641b4002d127028cc18adc99427135` | `014b02a` |
| `src/components/SegmentedControl/SegmentedControl.tsx` | `e242876037ba3076296547247b8dca1dfbdfa379757bf9be3295888d68f3502c` | `014b02a` |
| `src/components/SegmentedControl/index.ts` | `467e55626be16a333c984102df41e675246d65e8cf73f8e5c64bc79a62dc2862` | `014b02a` |
| `src/components/Select/Select.module.css` | `c3283206dda03d572530f85d48ba75b6b331d4fb3d6b9cd217b2645c38f9a130` | `014b02a` |
| `src/components/Select/Select.tsx` | `3403ca953883bc164deb6ba7c5c6fc76a96d2f581a82218b2c39d33e3bb2a7db` | `014b02a` |
| `src/components/Select/index.ts` | `3e26692cebfd796b7570e036a36878add4802024dcbf99b3a8b2627b379d23cf` | `014b02a` |
| `src/components/ShowAllButton/ShowAllButton.module.css` | `2963935c331d7a6fa6a0de7a7ae62a097c5e97ba90508bb700b63c71a5971600` | `014b02a` |
| `src/components/ShowAllButton/ShowAllButton.tsx` | `e1fef9a9d850d36bc134710509ab1c26bbada92943afbf96c960c5e4728ac78b` | `014b02a` |
| `src/components/ShowAllButton/index.ts` | `ca3feffcb0a3ee257b3e2a4f21eca36170a22cb0904b13592e9777d388aada29` | `014b02a` |
| `src/components/Sidebar/Sidebar.module.css` | `116ca92048f5b0c3ff8e5dd0942ea5d02d1edb3afbbe12231f971fa461b23a73` | `014b02a` |
| `src/components/Sidebar/Sidebar.tsx` | `c54c46354026de937b24e15f00791a943a691a9bf5019b370874de8dbc69d705` | `014b02a` |
| `src/components/Sidebar/index.ts` | `c8e8a42dc3f3f9c8647436e448ab48f2bc18eae7c3d00884ed3f706e7029ab54` | `014b02a` |
| `src/components/Skeleton/Skeleton.module.css` | `4492d0c0d3d808802bb24d7ffeb83ce1f4acb9be747968235e88e33b38fb97c9` | `014b02a` |
| `src/components/Skeleton/Skeleton.tsx` | `d767e3485d6db71bf79ebd8a6e40d621fa55b531987f66401049792fd62306ac` | `014b02a` |
| `src/components/Skeleton/index.ts` | `803b1971055c191a62c945fb8defc56601eda20f0b4976d75e70a6030a0f20a2` | `014b02a` |
| `src/components/SourceDialog/SourceDialog.module.css` | `97558bf759bd4805009fc0cb72ebca3d6b5e13a316348a1448de91ff95f331c7` | `014b02a` |
| `src/components/SourceDialog/SourceDialog.tsx` | `2cd42f034d84606589fcc03e191c09f58a7cbf75d33b3032493f6f3c65605b6a` | `014b02a` |
| `src/components/SourceDialog/index.ts` | `ea3b0f5e31667ff4ff15c2df63b3d3a7a1eed7a59ac856f04c3371426d2f3040` | `014b02a` |
| `src/components/SourceReference/SourceReference.module.css` | `e715e6ae4aead827958d33aaacebfb6ed17331a8926124f7b5be29391454eed8` | `014b02a` |
| `src/components/SourceReference/SourceReference.tsx` | `944e7cdd742e066f6340eef1225b378b24723888316d1ba0f5d7c8e78c047796` | `014b02a` |
| `src/components/SourceReference/index.ts` | `b6e9efd687355d801e3fc7798ef5e674151c349840654596171dbf57f655c41b` | `014b02a` |
| `src/components/StatusTag/StatusTag.module.css` | `c33c00c74a660fef6821ff829983ae58ac9ab9ea6a78d6d28562b38b9f26c763` | `014b02a` |
| `src/components/StatusTag/StatusTag.tsx` | `45a46527461c5fa91c2c14548976bbe62b7fda10632b9eeb5e32886844088115` | `014b02a` |
| `src/components/StatusTag/index.ts` | `e2924730f8e8ee30090dc4b07b5f4b3033b462042c458d28c2c8b00354b2fdd1` | `014b02a` |
| `src/components/TabBar/TabBar.module.css` | `74527c4bdfe937006ec65003bb36eb01d2a509e005f8088674b974ab3e715726` | `014b02a` |
| `src/components/TabBar/TabBar.tsx` | `375834fd583237825a747e82043b3d14eddd28148a217ee54dfcf1abad243941` | `014b02a` |
| `src/components/TabBar/index.ts` | `de3d2a54008c67d655dd185119eb32ad21b141d3a3c0e91f69918b58749a2d98` | `014b02a` |
| `src/components/Table/Table.module.css` | `f14d444da7a8b98071ad155a1ee022c42a747492b4fc9d97988e65710bc6c39d` | `014b02a` |
| `src/components/Table/Table.tsx` | `c0529bb97af52305c100390834806edc2b0197d19186733ad48ea72294fa0f8e` | `014b02a` |
| `src/components/Table/index.ts` | `8e0cfdcde865955aca0ce561805c70875cc6acf0985661755a99a97a6cc08882` | `014b02a` |
| `src/components/Tabs/Tabs.module.css` | `4adc7a9ddb5c9c6aad1b56dfeeb8525c3b7619d3111c8c50c7e1c45f64ed0050` | `014b02a` |
| `src/components/Tabs/Tabs.tsx` | `68dc7f183f4e73c6e20fd97f4a7e2c546a3635b2dd327160d8aad0424b2160e1` | `014b02a` |
| `src/components/Tabs/index.ts` | `5dd96b96db0294e69fcf281dc61515d0db54a2535dd66352c19a28bdcfd2e49b` | `014b02a` |
| `src/components/Tag/Tag.module.css` | `931d07cecccd40fde9f40378fa1fa5109c315b33249a49696a8acc873e3ab5ee` | `014b02a` |
| `src/components/Tag/Tag.tsx` | `5e57d7a90dafb55d580b9509622f1ed48ce9d20cbc242b46319fecd9b051d5dd` | `014b02a` |
| `src/components/Tag/index.ts` | `be7b98a54dc322ab797a6ed6e230febc5ed893408ffabafbc26a51c602bcc1b0` | `014b02a` |
| `src/components/TaskItem/TaskItem.module.css` | `83f7b86838b9998075d3b42b750d948d72c1d83c728f7713a3b13d30e80e97c3` | `014b02a` |
| `src/components/TaskItem/TaskItem.tsx` | `7d4e84dee8b3914f40072a585e67728e58bf4da9e57b843e223b68a36f07fc4a` | `014b02a` |
| `src/components/TaskItem/index.ts` | `7bca242a2471fce7823b9d3eb4cfbefac7281bc59c9c28cca04a87156e09208e` | `014b02a` |
| `src/components/Textarea/Textarea.module.css` | `932216d12e2696397d4d309308f7d635fba9c1e995576d6bd864df0086dc188f` | `014b02a` |
| `src/components/Textarea/Textarea.tsx` | `36b4080f045c1a76dac0126e13af488d60069c4b172d8bcf40540f96db5e7308` | `014b02a` |
| `src/components/Textarea/index.ts` | `3238a82685fc10a634a42266c3c41851cacba8803c97ea2113a61e4c95a7d1d7` | `014b02a` |
| `src/components/TimePeriod/TimePeriod.module.css` | `5bca00346091239c516ffcce11a85b739c00a59bdcde59745481622a8f394a74` | `014b02a` |
| `src/components/TimePeriod/TimePeriod.tsx` | `b38ac5657b1f2608fcd52c684c7cf13008a8629e0120f9d1ba40edcec7d9db1b` | `014b02a` |
| `src/components/TimePeriod/index.ts` | `e0bfabb0ebcd89ab8247d61849ba936c35d325f1d1bd83b07413fadb2d5dfafb` | `014b02a` |
| `src/components/TimelineEntry/TimelineEntry.module.css` | `b3dd308ee927e32c6fcd241f0eb8c4342c41a23dce7eea243f97e09aed32f316` | `014b02a` |
| `src/components/TimelineEntry/TimelineEntry.tsx` | `66a808d6e7b9772b3183d21f239944540d8bfbac3b12afc913725a286ab6bf15` | `014b02a` |
| `src/components/TimelineEntry/index.ts` | `8faee095a58fd6cc6cb51a08bf46787ced55ad7d10ad1aa3c058662e994a682d` | `014b02a` |
| `src/components/TimelineMark/TimelineMark.module.css` | `87c12f1a75ed6d97d84da8a31ff58c40a9524de36dab59df43d263b4c82f524f` | `014b02a` |
| `src/components/TimelineMark/TimelineMark.tsx` | `5e012758741c747ca29c12ed3faaaeac12b2522bd78927c8592cae1b90b0a63b` | `014b02a` |
| `src/components/TimelineMark/index.ts` | `b43cb2e714ebf7b4cbd66a0cbbcc9cd28a2bcef93329c575b94714966973bee2` | `014b02a` |
| `src/components/TimelineRow/TimelineRow.module.css` | `7db4a9e4d6b4442610d2fdf1d6dc96a4fe8e8022aeca29f01f747a46541a24db` | `014b02a` |
| `src/components/TimelineRow/TimelineRow.tsx` | `ecb768ee07909e186534dbbcf3af71dd9f0e9dda8b8018d7a4823b5bd0c60265` | `014b02a` |
| `src/components/TimelineRow/dates.ts` | `3a75470b2a2395ac1be63ab14f379980b1ebc191ac4822c5be808bccf4060bcd` | `014b02a` |
| `src/components/TimelineRow/index.ts` | `953f5cfa4507da708485c1a64090358997d8d1772933efd410e3a1aea8eeeb15` | `014b02a` |
| `src/components/TimelineTodayLine/TimelineTodayLine.module.css` | `840a6ccca477813809603154ae8f2284d09cfe41c5c09c69163637c066ced4bd` | `014b02a` |
| `src/components/TimelineTodayLine/TimelineTodayLine.tsx` | `91eae3f2bfe6d608823993f772cf836892c08d7b46d013ccbc83481f532d9ac0` | `014b02a` |
| `src/components/TimelineTodayLine/index.ts` | `f47f1d677dc39312aca574c6f79e36f5e44c4f354d65391fc00918c980e23a83` | `014b02a` |
| `src/components/TodayBriefing/TodayBriefing.module.css` | `ae4c7b4a8d1a545c8ef0764be48b6eabc89262eef6fcf265bbb8ad0399f91cd1` | `014b02a` |
| `src/components/TodayBriefing/TodayBriefing.tsx` | `87935017430ea7ac4313faec3baecc0974763785de2e416b6f1f21292cda4822` | `014b02a` |
| `src/components/TodayBriefing/index.ts` | `d6fbd76ee26b14d0a596201d93b68e14c921525751d066b5067ba1ad588506c6` | `014b02a` |
| `src/components/Toggle/Toggle.module.css` | `5a0807ec77765ef2ceeefeaf71d3ff06c970153d74c3f86da74da41a36725748` | `014b02a` |
| `src/components/Toggle/Toggle.tsx` | `3ea1c4213b2b4962035ddd0169672f5695494911986a784ac4d2e37ce38ee287` | `014b02a` |
| `src/components/Toggle/index.ts` | `ac23395eb3e61b8a3be6c1cfb7bb92c34670fae470441092004a0b0f1bd7568e` | `014b02a` |
| `src/components/Tooltip/Tooltip.module.css` | `fe400cae6efb01a321bedb2b17f9ea6982a9152a0847c82c7759e9ef6ea51102` | `014b02a` |
| `src/components/Tooltip/Tooltip.tsx` | `3660934dfa320e17fd5705f8f2ef66a5ced28c31cbd76f27bc07be7765bcef57` | `014b02a` |
| `src/components/Tooltip/index.ts` | `25c383c2ddf3b5eb16050e572ddbda41c383dbff24f9cbb555d44ee6dafdcb9b` | `014b02a` |
| `src/components/Tooltip/placeFixed.ts` | `0a661d796e83dd816b1b9a240c6e20dd9433d9027c9b2475905ac3fe8f9834a9` | `014b02a` |
| `src/components/TypedConfirmation/TypedConfirmation.module.css` | `2a06a73badaf0bf889d0b00070fe12232688e488818a8d902e36265c9ec2eb58` | `014b02a` |
| `src/components/TypedConfirmation/TypedConfirmation.tsx` | `5788d33bf49a7808167e63410928ccf3dbe299d454f94a6ffb39842a37d66129` | `014b02a` |
| `src/components/TypedConfirmation/index.ts` | `4b069c9c7ffceefc31b6dec3d89cd4bddcfb195bbdadb6b8cf7d1403bcd0871f` | `014b02a` |
| `src/components/UserMenuItem/UserMenuItem.module.css` | `97fd06d5f7f64c54dcb6237d2eea67bd74b953141178b142ca67b1ead661be79` | `014b02a` |
| `src/components/UserMenuItem/UserMenuItem.tsx` | `84b7950b94abf4abe5860de6bd74009c4422ebfd780e8d2734314f7f485947ee` | `014b02a` |
| `src/components/UserMenuItem/index.ts` | `9b2c647f60cbd97c6aa8678d42e05af4d800b7686ab0d9b83a1488b9875d4d19` | `014b02a` |
| `src/components/UserSwitcher/UserSwitcher.module.css` | `b2d1bef61381730fab218252977fc5c7a29dd498a5409ca12cad3cc7e2e03563` | `014b02a` |
| `src/components/UserSwitcher/UserSwitcher.tsx` | `76399c835297d6fa6e259b114d06b2460baba51a8323eac9c1a9aca54bf34bc2` | `014b02a` |
| `src/components/UserSwitcher/index.ts` | `20e50309025b8789d07f5d004fa8cbd6712a387aa2dc6e2a068a931f3b4628b9` | `014b02a` |
| `src/components/VisitRhythm/VisitRhythm.module.css` | `b8763bf5bcd700ac24064a5bddac6309f75c62cdc99a01d6129587a3be024059` | `014b02a` |
| `src/components/VisitRhythm/VisitRhythm.tsx` | `668a9bd69b65ecd861abcea61aaeda5215a731c81d2f94956d028de4998dbb8f` | `014b02a` |
| `src/components/VisitRhythm/index.ts` | `8f50590d1f3fefe42bf4b15f3e59c4fabd540188e7794abec268b45a38022f1c` | `014b02a` |
| `src/components/VisitRhythm/rhythm.ts` | `979b2f482f3d386692bb12bb0271b207e633abef9bf9eb4ec1854e7c818e2efc` | `014b02a` |
| `src/index.ts` | `075dcd3c9b5dac86db4985f1c83d436a2ac463101e7b0800fd7846181535ddfb` | `014b02a` |
| `src/styles/base.css` | `2d9c13b52db406a734c05ab21ad2cbaae07fe8ca147a228b069a284f00fa6d57` | `014b02a` |
| `src/styles/index.css` | `1f34c902817cadd66c90585cd5bb8a8804805e341d894360c2b22d7907e155b5` | `014b02a` |
| `src/styles/tokens.css` | `c98bacdf1d4cec0236ceeeaf1ede2c81a267f7d5eab8700b91ee5ac66f6a804c` | `014b02a` |
| `tokens/dtcg/color.dark.json` | `c7e285d716661b065aed12fd7d419ae5ed3fa360266e67e8f6ee3824b3a09554` | `014b02a` |
| `tokens/dtcg/color.light.json` | `da0b7c6c444ff8756527af2f57b34a15f0fec4163fbc8c32a695b85090e77905` | `014b02a` |
| `tokens/dtcg/dimension.json` | `c09f223845ffa0c7b695d0b8293e3b454c63bc399ec667613ad27b806e91255e` | `014b02a` |
| `tokens/dtcg/primitives.json` | `cbbfccd3d9a2382393a7229fb7507982cb72de69980ceab3dae62505b43476ac` | `014b02a` |
| `tokens/dtcg/system.json` | `0c489f362bdab61ca01cb88b07aa507547572c9b1fe81b7f37298177a4123953` | `014b02a` |
| `tokens/dtcg/typography.json` | `2a6356ae6c155f061ea13d0e04a9706c942d26e3ed15abd55f3b854233254d72` | `014b02a` |
| `tokens/figma-export.json` | `4513494d4c006f3081a7292e596ddc19f561dd51c95765708fad031f33ea9d85` | `014b02a` |
| `tokens/styleguide-only.json` | `9acbe24e8158187fdfd4e860c74d40d72d46f4770e98362d515c343a919405fa` | `014b02a` |
