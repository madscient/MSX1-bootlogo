# MSX1-bootlogo 作業ガイド（AI 向け）

MSX1 用の 16KB カートリッジ ROM（起動ロゴのアニメーション）。Z80 アセンブリのソース 1 本と、Python の補助スクリプトでできている。

## 文書の役割

| 文書 | 読者 | 書くこと |
|---|---|---|
| `README.md` | 利用者 | 使い方、動作、ビルドの手順 |
| `docs/technical.md` | 開発者（人間） | 仕様、設計、検証状況 |
| `CLAUDE.md` | AI | 作業の手順と決まり |
| `docs/ai/history.md` | AI | 経緯、決定の理由と前提、見送った提案 |

- 人間向けの 2 文書には現在の状態だけを書く。経緯や履歴（「以前は〜だった」「〜を直した」）を書いてよいのは `docs/ai/history.md` だけ。
- `README.md` には AI 向け文書への参照を書かない。
- 仕様や手順を変えたら、同じ対象に触れている箇所を 4 文書すべてで検索して合わせる。ビルドと検証のコマンドは `README.md` とこの文書に、検証の範囲と確度は `docs/technical.md` の「検証状況」にある。
- 決定をしたら、理由と前提（何が変わらない限り成立するか）を `docs/ai/history.md` に書く。見送った提案も理由つきで同じ文書に書く。

## 構成

| パス | 内容 |
|---|---|
| `src/logo.asm` | ROM 全体のソース |
| `assets/*.dat` | ソースが `incbin` で取り込むデータ。`tools/gen_assets.py` の出力 |
| `tools/build.py` | アセンブラを実行し、FFh で 16KB に埋めて ROM にする |
| `tools/gen_assets.py` | `assets/` の生成と照合（`--check`） |
| `tools/verify.py` | BIOS を持たない模擬環境で ROM を実行して検査する |
| `tools/test_openmsx.py` | openMSX で ROM を起動して検査する。`tools/openmsx_test.tcl` は openMSX の中で記録を取る側 |
| `docs/images/preview.gif` | README に載せる画像。`tools/verify.py` の出力の写し |
| `build/` | ビルドと検証の出力先。追跡対象外 |

## コマンド

```text
python tools/build.py              ビルド（--asm zmac / sjasmplus / pasmo で指定）
python tools/verify.py             模擬環境での検証（20 秒ほど）
python tools/test_openmsx.py       openMSX での検証（10 秒ほど。--machine 機種名 で 1 機種）
python tools/gen_assets.py --check assets/ が生成元と一致するか
```

外部のプログラムは環境変数から、なければ PATH から探す。

| 環境変数 | 内容 |
|---|---|
| `ZMAC_EXE`、`SJASMPLUS_EXE`、`PASMO_EXE` | 各アセンブラの実行ファイルのパス |
| `OPENMSX_DIR` | openMSX の実行ファイルがあるフォルダ |
| `MSX_ROM_DIRS` | 純正 BIOS などのシステム ROM を探すフォルダ（PATH と同じ区切り）。`--machine` で純正機種を試すときに使う |

- 検証スクリプトは、検査に通らなければ 0 以外の終了コードで終わる。結果のファイルが前回のまま残ることがあるので、合否は終了コードで判断する。
- 現在の ROM には不合格の検査が 2 種類ある（`docs/technical.md` の「検証状況」）。`tools/test_openmsx.py` の既定の構成では 1 件、純正 BIOS の機種では起動ごとに 1 件が不合格になる。これ以外の不合格は新しい問題である。

## 変更するときの決まり

- **スクリプトと文書にローカルのパスを書かない。** 外部のプログラムやフォルダの場所が要るときは、環境変数を経由する。新しく要るものが出たら、上の表の命名にならう。
- **ROM の内容を変えないつもりの変更は、ビルド結果の SHA-256 が変わらないことで確かめる。** 基準の値は `docs/technical.md` の「検証状況」にある。検証スクリプトは実行時に参照されないバイトの変化を検出しないので、同一性の確認には使えない。
- **ROM の内容を変えたら、`docs/technical.md` の「検証状況」の SHA-256 と表を更新する。** 新しい ROM で確かめ直していない行を「確認済み」のまま残さない。
- **ソースは zmac、sjasmplus、Pasmo の 3 つが受け付ける構文だけで書く。** マクロ、`assert`、`device` など一部にしかないものは使わない。zmac は `ds` の埋め値に 128 以上を受け付けない。3 つすべてでビルドしていないときは、文書の「確認済み」をその範囲に留める。
- **`incbin` のパスはルートからの相対（`assets/...`）のままにする。** `../assets/...` の形は Pasmo が解決できない。
- **新しい Z80 命令や BIOS 呼び出しを使ったら、`tools/verify.py` の `step()` と `bios()` に足す。** 模擬環境はこの ROM が使うものしか実装していない。未対応のものに出会うと AssertionError で止まる。
- **ラベル名、作業 RAM のアドレス、段階の数を変えるときは、`tools/verify.py`、`tools/test_openmsx.py`、`tools/openmsx_test.tcl` を検索する。** ラベル名、`0xD8..` のアドレス、`154` などの数を参照している。
- **`assets/*.dat` を直接編集しない。** `tools/gen_assets.py` を変えて書き出し、`--check` で一致を確かめる。乱数の呼び出し順を変えると `beams.dat` 全体が変わる。
- **アニメーションの見た目を変えたら、`build/preview.gif` を `docs/images/preview.gif` へコピーする。** 検証スクリプトは `build/` にしか書かない。
- **`build/` の中身はコミットしない。** openMSX 用に作る機種定義や設定にはローカルのパスが入る。
- **openMSX を試しに直接起動するときは、環境変数 `OPENMSX_USER_DATA` を `build/` の中のフォルダに向ける。** 向けずに起動すると、利用者自身の openMSX のユーザーデータ（キャッシュや画面配置のファイル）が書き換わる。

## ROM のコードで守ること

理由は `docs/technical.md` の該当する節と、ソースのコメントにある。

- D8A0h 以上に書き込まない。
- page 3 のアドレスに RDSLT / WRSLT を使わない。page 3 の副スロットは FFFFh を直接読んで求める。
- VDP への直接書き込みは、アドレスとデータの全体を DI / EI で囲む。
- INIT は BIOS から渡されたスタックのまま RET で戻る。終了時に作業 RAM を消去する。
