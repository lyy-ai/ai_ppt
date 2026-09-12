# 国内聚合支付与会员权益

国内版本采用“聚合收银台 + 异步支付回调”。PPT Master 创建订单并保存待支付状态，聚合支付平台负责支付宝/微信收款，只有验签、验金额并确认支付成功后才发放会员权益。

## 配置

生产环境至少配置：

```env
PPT_MASTER_PAYMENT_PROVIDER=aggregator
PPT_MASTER_YPAY_BASE_URL=https://pay.example.com
PPT_MASTER_YPAY_PID=<merchant-pid>
PPT_MASTER_YPAY_KEY=<merchant-key>
PPT_MASTER_YPAY_TYPE=alipay
PPT_MASTER_YPAY_NOTIFY_URL=https://api.aigcstory.site/billing/ypay/notify
PPT_MASTER_YPAY_RETURN_URL=https://ppt-cn.aigcstory.site/cloud-generator.html
PPT_MASTER_BILLING_WEBHOOK_SECRET=<long-random-secret>
PPT_MASTER_BILLING_AUTO_COMPLETE=0
```

如果聚合平台不是 YPay 兼容协议，则改用：

```env
PPT_MASTER_PAYMENT_PROVIDER=aggregator
PPT_MASTER_AGGREGATOR_CHECKOUT_URL_TEMPLATE=<provider-checkout-template>
PPT_MASTER_AGGREGATOR_NOTIFY_URL=https://api.aigcstory.site/billing/webhook
PPT_MASTER_BILLING_WEBHOOK_SECRET=<long-random-secret>
```

## 回调入口

- YPay 兼容：`POST /billing/ypay/notify`
- 通用 JSON 回调：`POST /billing/webhook`

回调处理会验证签名、商户标识、订单号和支付金额，并按 `provider_event_id` 做幂等处理。支付成功后才把用户计划升级为 Plus、Pro 或 Team。

## 上线前检查

```bash
PPT_MASTER_DEPLOYMENT=production python3 scripts/check-cloud-generator-readiness.py
```

不要把商户 Key、回调密钥或服务器环境文件提交到 Git。正式上线前应使用支付平台沙箱完成：成功支付、重复回调、金额篡改、失败支付和退款场景测试。
