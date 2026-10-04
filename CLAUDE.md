# MSX1-bootlogo 作業ガイド（AI 向け）

MSX1 用の 16KB カートリッジ ROM（起動ロゴのアニメーション）。Z80 アセンブリのソースと、Python の補助スクリプトでできている。同じソースから、ページ 1（4000h）用とページ 2（8000h）用の 2 つのイメージを作る。

## 文書の役割

| 文書 | 読者 | 書くこと |
|---|---|---|
| `README.md` | 利用者 | 使い方、動作、ビルドの手順 |
| `README_en.md` | 利用者（英語） | `README.md` の英訳。構成と内容を `README.md` に合わせる |
| `docs/technical.md` | 開発者（人間） | 仕様、設計、検証状況 |
| `CLAUDE.md` | AI | 作業の手順と決まり |
| `docs/ai/history.md` | AI | 経緯、決定の理由と前提、見送った提案 |

- 人間向けの 3 文書には現在の状態だけを書く。経緯や履歴（「以前は〜だった」「〜を直した」）を書いてよいのは `docs/ai/history.md` だけ。
- `README.md` と `README_en.md` には AI 向け文書への参照を書かない。
- 仕様や手順を変えたら、同じ対象に触れている箇所を 5 文書すべてで検索して合わせる。`README.md` を変えたら `README_en.md` も同じように変える。ビルドと検証のコマンドは 2 つの README とこの文書に、検証の範囲と確度は `docs/technical.md` の「検証状況」にある。
- 決定をしたら、理由と前提（何が変わらない限り成立するか）を `docs/ai/history.md` に書く。見送った提案も理由つきで同じ文書に書く。

## 構成

| パス | 内容 |
|---|---|
| `src/logo.asm` | ROM 全体のソース |
| `src/page1.asm`、`src/page2.asm` | イメージごとの入口。`rom_base` を定義して `src/logo.asm` を取り込むだけ |
| `assets/*.dat` | ソースが `incbin` で取り込むデータ。`tools/gen_assets.py` の出力 |
| `tools/build.py` | アセンブラを実行し、FFh で 16KB に埋めて ROM にする。ページと出力ファイル名の表（`PAGES`）はここにあり、検証スクリプトもここから読む |
| `tools/gen_assets.py` | `assets/` の生成と照合（`--check`） |
| `tools/verify.py` | BIOS を持たない模擬環境で ROM を実行して検査する |
| `tools/test_openmsx.py` | openMSX で ROM を起動して検査する。`tools/openmsx_test.tcl` は openMSX の中で記録を取る側 |
| `tools/screen.py` | 2 つの検証スクリプトが共有する、レーザー区間の画面の期待値とスプライトの表示規則 |
| `docs/images/preview.gif` | README に載せる画像。`tools/verify.py` の出力の写し |
| `build/` | ビルドと検証の出力先。追跡対象外 |

## コマンド

```text
python tools/build.py              2 つのイメージをビルド（--asm zmac / sjasmplus / pasmo、--page 1 / 2 で指定）
python tools/verify.py             模擬環境での検証（2 つのイメージで 20 秒ほど。--page で片方）
python tools/test_openmsx.py       openMSX での検証（2 つのイメージで 30 秒ほど。--page で片方）
python tools/test_openmsx.py --machine 機種名 [--ram スロット:容量] [--rom alone / neighbour / mirrored]
                                   openMSX のその機種で 1 回。--ram は RAM の置き換え（例: 0-2:64）、
                                   --rom はカートリッジのスロットのもう一方のページの状態
python tools/gen_assets.py --check assets/ が生成元と一致するか
```

外部のプログラムは環境変数から、なければ PATH から探す。

| 環境変数 | 内容 |
|---|---|
| `ZMAC_EXE`、`SJASMPLUS_EXE`、`PASMO_EXE` | 各アセンブラの実行ファイルのパス |
| `OPENMSX_DIR` | openMSX の実行ファイルがあるフォルダ |
| `MSX_ROM_DIRS` | 純正 BIOS などのシステム ROM を探すフォルダ（PATH と同じ区切り）。`--machine` で純正機種を試すときに使う |

- 検証スクリプトは、検査に通らなければ 0 以外の終了コードで終わる。結果のファイルが前回のまま残ることがあるので、合否は終了コードで判断する。
- 現在の ROM に、既知の不合格はない。2 つのスクリプトの既定の実行は、どちらも終了コード 0 になる。不合格が出たら新しい問題である。
- ROM がもう一方のページにミラーされる構成は、保証の対象外である（依頼者の決定）。`--rom mirrored` はその構成を試すためのもので、INIT を 2 回呼ぶ BIOS では「ちょうど 1 回再生される」の検査が不合格になる。これは不具合として扱わない。

## 変更するときの決まり

- **スクリプトと文書にローカルのパスを書かない。** 外部のプログラムやフォルダの場所が要るときは、環境変数を経由する。新しく要るものが出たら、上の表の命名にならう。
- **ROM の内容を変えないつもりの変更は、ビルド結果の SHA-256 が変わらないことで確かめる。** 基準の値は `docs/technical.md` の「検証状況」に、イメージごとにある。検証スクリプトは実行時に参照されないバイトの変化を検出しないので、同一性の確認には使えない。
- **ROM の内容を変えたら、`docs/technical.md` の「検証状況」の SHA-256 と表を更新する。** 新しい ROM で確かめ直していない行を「確認済み」のまま残さない。検証は 2 つのイメージの両方で行う。
- **ソースは zmac、sjasmplus、Pasmo の 3 つが受け付ける構文だけで書く。** マクロ、`assert`、`device` など一部にしかないものは使わない。zmac は `ds` の埋め値に 128 以上を受け付けない。コマンドラインから定数の値を渡す方法は 3 つで共通にできない（zmac の `-D` は 1 を定義するだけで、値を渡す `-P` は zmac だけの書き方になる）。3 つすべてでビルドしていないときは、文書の「確認済み」をその範囲に留める。
- **`include` と `incbin` のパスはルートからの相対（`src/...`、`assets/...`）のままにする。** `../assets/...` の形は Pasmo が解決できない。
- **ページによって変わる値は `rom_base` から計算する。** 4000h や 8000h、ページ番号をソースに直接書かない。
- **新しい Z80 命令や BIOS 呼び出しを使ったら、`tools/verify.py` の `step()` と `bios()` に足す。** 模擬環境はこの ROM が使うものしか実装していない。未対応のものに出会うと AssertionError で止まる。
- **ラベル名、作業 RAM の変数名（`w_...`）、段階の数を変えるときは、`tools/verify.py`、`tools/test_openmsx.py`、`tools/openmsx_test.tcl`、`tools/screen.py` を検索する。** ラベルと変数は名前で、段階の数は `154` や `36` などの数で参照している。作業 RAM のアドレスそのものはシンボルから読むので、`work` を動かすだけなら検証スクリプトの変更は要らない（文書の表は要る）。
- **レーザーの色、色のずらし方、切り替えの間隔、黒い矩形の範囲を変えるときは、`tools/screen.py` の定数と期待値も変える。** 検証側は ROM の値を読まず、仕様の値を自分で持っている。
- **`assets/*.dat` を直接編集しない。** `tools/gen_assets.py` を変えて書き出し、`--check` で一致を確かめる。乱数の呼び出し順を変えるとレーザーの動き全体が変わる。
- **アニメーションの見た目を変えたら、`build/preview.gif` を `docs/images/preview.gif` へコピーする。** 検証スクリプトは `build/` にしか書かない。
- **`build/` の中身はコミットしない。** openMSX 用に作る機種定義や設定にはローカルのパスが入る。
- **openMSX を試しに直接起動するときは、環境変数 `OPENMSX_USER_DATA` と `OPENMSX_HOME` の両方を `build/` の中のフォルダに向ける。** 向けずに起動すると、利用者自身の openMSX のファイルが書き換わる。`OPENMSX_USER_DATA` だけでは、時計チップや電池つき RAM を持つ機種の保存ファイル（`persistent/`）が利用者側に書かれる。
- **リリースには `msx_logo_page1.rom` と `msx_logo_page2.rom` の両方を同梱する。** 同梱する前に、2 つの SHA-256 が `docs/technical.md` の「検証状況」の値と一致することを確かめる。

## ROM のコードで守ること

理由は `docs/technical.md` の該当する節と、ソースのコメントにある。

- 作業 RAM（`work` から `work_size` バイト。E000h–E05Fh）以外の RAM に書き込まない。E000h より下は、RAM が 8KB の機種には無い。
- page 3 のアドレスに RDSLT / WRSLT を使わない。page 3 の副スロットは FFFFh を直接読んで求める。
- BIOS がある主スロット（スロット 0）の page 0 に RDSLT / WRSLT を使わない。BIOS が自分自身を page 0 から外してしまう。
- BIOS と同じスロットの page 0 と page 1（システム ROM）を読み書きしない。エミュレータの機種定義によっては、ROM への書き込みでバンクが切り替わる。
- 副スロットレジスタ（FFFFh）を書き換えるのは、割り込みを禁止した中で、page 0 のぶんだけにする。BIOS を呼ぶ前に元へ戻す。
- ROM がミラーされる構成への対策（2 回目の INIT を見分ける処理）は入れない。
- VDP への直接書き込みは、アドレスとデータの全体を DI / EI で囲む。データを続けて書くときは `vram_out` を呼んで 1 バイトずつ書く（書き込みの間隔を保つため）。
- INIT は BIOS から渡されたスタックのまま RET で戻る。終了時に作業 RAM を消去し、スプライトの大きさの設定を元に戻す。
- 1 本の走査線に並ぶスプライトを 4 枚以下にする。横のレーザーをスプライトにしない。
