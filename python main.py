# Puraudhia Manager — GitHubデプロイ完成版

このフォルダ内のファイルを、新しく作る専用GitHubリポジトリへすべてアップロードしてください。

## 組み込み済みID
- GUILD_ID: `1458016711344263170`
- ADMIN_ROLE_ID: `1468161318635962451`
- MANAGEMENT_LOG_CHANNEL_ID: `1523582826623008863`
- JOIN_LEAVE_LOG_CHANNEL_ID: `1472429718144811050`
- DASHBOARD_CHANNEL_ID: `1467734039883677856`

## 環境変数
ホスティング側に次だけ登録します。

```text
DISCORD_TOKEN=新しい管理Botのトークン
```

SQLiteを永続保存する場合は `DB_PATH` も設定します。

## 起動コマンド
```bash
python main.py
```

## Developer Portal
- Server Members Intent: ON
- Presence Intent: OFFでOK
- Message Content Intent: OFFでOK

## Bot権限
- チャンネルを見る
- メッセージを送信
- 埋め込みリンク
- メッセージ履歴を読む
- 監査ログを表示

## 最初に実行
```text
/管理ダッシュボード
```

## 機能
- VC入退室・移動・滞在時間
- 今日・週間・月間統計
- VCランキング
- 加入・退出・Kick・BAN・Unbanログ
- ロール・ニックネーム・タイムアウト変更ログ
- 面接記録
- メンバーカルテ
- 警告・メモ
- 新規VC未参加一覧
- VC休眠一覧
- 自動更新ダッシュボード
- 日次レポート
- CSV出力

## 注意
GitHubにはトークンを絶対に書かないでください。
