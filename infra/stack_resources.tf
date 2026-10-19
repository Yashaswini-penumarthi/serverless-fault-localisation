# Faultlab serverless fault detection / localisation research stack.
# Tags: project slug only — never personal name or personal ID.

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project    = "faultlab"
      managed_by = "terraform"
      purpose    = "research-eval"
      data       = "synthetic"
    }
  }
}

locals {
  tracing_on = var.tracing_mode == "Active"
  packages_ready = (
    fileexists(var.layer_package) &&
    fileexists(var.orders_api_package) &&
    fileexists(var.inventory_package) &&
    fileexists(var.payments_package) &&
    fileexists(var.notifications_package)
  )
}

resource "aws_dynamodb_table" "orders" {
  name         = "${var.name_prefix}-orders"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"

  attribute {
    name = "pk"
    type = "S"
  }
}

resource "aws_ssm_parameter" "fault" {
  name  = "/${var.name_prefix}/fault"
  type  = "String"
  value = "{}"
}

data "aws_iam_policy_document" "assume_lambda" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "fn" {
  name               = "${var.name_prefix}-fn"
  assume_role_policy = data.aws_iam_policy_document.assume_lambda.json
}

data "aws_iam_policy_document" "fn" {
  statement {
    sid       = "Dynamo"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan"]
    resources = [aws_dynamodb_table.orders.arn]
  }

  statement {
    sid       = "FaultParam"
    actions   = ["ssm:GetParameter"]
    resources = [aws_ssm_parameter.fault.arn]
  }

  statement {
    sid       = "InvokePeers"
    actions   = ["lambda:InvokeFunction"]
    resources = ["arn:aws:lambda:${var.region}:*:function:${var.name_prefix}-*"]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.region}:*:log-group:/aws/lambda/${var.name_prefix}-*:*"]
  }

  statement {
    sid       = "XRay"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "fn" {
  name   = "faultlab"
  role   = aws_iam_role.fn.id
  policy = data.aws_iam_policy_document.fn.json
}

resource "aws_cloudwatch_log_group" "orders_api" {
  name              = "/aws/lambda/${var.name_prefix}-orders-api"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "inventory" {
  name              = "/aws/lambda/${var.name_prefix}-inventory"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "payments" {
  name              = "/aws/lambda/${var.name_prefix}-payments"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "notifications" {
  name              = "/aws/lambda/${var.name_prefix}-notifications"
  retention_in_days = 14
}

resource "aws_lambda_layer_version" "faultlab" {
  count                    = local.packages_ready ? 1 : 0
  layer_name               = "${var.name_prefix}-faultlab"
  filename                 = var.layer_package
  source_code_hash         = filebase64sha256(var.layer_package)
  compatible_runtimes      = ["python3.12"]
  compatible_architectures = ["arm64"]
}

resource "aws_lambda_function" "inventory" {
  count = local.packages_ready ? 1 : 0

  function_name    = "${var.name_prefix}-inventory"
  role             = aws_iam_role.fn.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "app.handler"
  filename         = var.inventory_package
  source_code_hash = filebase64sha256(var.inventory_package)
  memory_size      = var.memory_mb
  timeout          = 5
  layers           = [aws_lambda_layer_version.faultlab[0].arn]
  tracing_config { mode = var.tracing_mode }

  environment {
    variables = {
      TABLE_NAME           = aws_dynamodb_table.orders.name
      FAULT_PARAM          = aws_ssm_parameter.fault.name
      LOG_LEVEL            = var.log_level
      CLIENT_TIMEOUT_MS    = tostring(var.client_timeout_ms)
      FAULT_CACHE_S        = "2"
      AWS_XRAY_SDK_ENABLED = local.tracing_on ? "true" : "false"
    }
  }

  depends_on = [aws_cloudwatch_log_group.inventory, aws_iam_role_policy.fn]
}

resource "aws_lambda_function" "payments" {
  count = local.packages_ready ? 1 : 0

  function_name    = "${var.name_prefix}-payments"
  role             = aws_iam_role.fn.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "app.handler"
  filename         = var.payments_package
  source_code_hash = filebase64sha256(var.payments_package)
  memory_size      = var.memory_mb
  timeout          = 5
  layers           = [aws_lambda_layer_version.faultlab[0].arn]
  tracing_config { mode = var.tracing_mode }

  environment {
    variables = {
      TABLE_NAME           = aws_dynamodb_table.orders.name
      FAULT_PARAM          = aws_ssm_parameter.fault.name
      LOG_LEVEL            = var.log_level
      CLIENT_TIMEOUT_MS    = tostring(var.client_timeout_ms)
      FAULT_CACHE_S        = "2"
      AWS_XRAY_SDK_ENABLED = local.tracing_on ? "true" : "false"
    }
  }

  depends_on = [aws_cloudwatch_log_group.payments, aws_iam_role_policy.fn]
}

resource "aws_lambda_function" "notifications" {
  count = local.packages_ready ? 1 : 0

  function_name    = "${var.name_prefix}-notifications"
  role             = aws_iam_role.fn.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "app.handler"
  filename         = var.notifications_package
  source_code_hash = filebase64sha256(var.notifications_package)
  memory_size      = var.memory_mb
  timeout          = 5
  layers           = [aws_lambda_layer_version.faultlab[0].arn]
  tracing_config { mode = var.tracing_mode }

  environment {
    variables = {
      TABLE_NAME           = aws_dynamodb_table.orders.name
      FAULT_PARAM          = aws_ssm_parameter.fault.name
      LOG_LEVEL            = var.log_level
      CLIENT_TIMEOUT_MS    = tostring(var.client_timeout_ms)
      FAULT_CACHE_S        = "2"
      AWS_XRAY_SDK_ENABLED = local.tracing_on ? "true" : "false"
    }
  }

  depends_on = [aws_cloudwatch_log_group.notifications, aws_iam_role_policy.fn]
}

resource "aws_lambda_function" "orders_api" {
  count = local.packages_ready ? 1 : 0

  function_name    = "${var.name_prefix}-orders-api"
  role             = aws_iam_role.fn.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "app.handler"
  filename         = var.orders_api_package
  source_code_hash = filebase64sha256(var.orders_api_package)
  memory_size      = var.memory_mb
  timeout          = 5
  layers           = [aws_lambda_layer_version.faultlab[0].arn]
  tracing_config { mode = var.tracing_mode }

  environment {
    variables = {
      TABLE_NAME           = aws_dynamodb_table.orders.name
      FAULT_PARAM          = aws_ssm_parameter.fault.name
      LOG_LEVEL            = var.log_level
      CLIENT_TIMEOUT_MS    = tostring(var.client_timeout_ms)
      FAULT_CACHE_S        = "2"
      AWS_XRAY_SDK_ENABLED = local.tracing_on ? "true" : "false"
      INVENTORY_FN         = aws_lambda_function.inventory[0].function_name
      PAYMENTS_FN          = aws_lambda_function.payments[0].function_name
      NOTIFICATIONS_FN     = aws_lambda_function.notifications[0].function_name
    }
  }

  depends_on = [aws_cloudwatch_log_group.orders_api, aws_iam_role_policy.fn]
}

resource "aws_api_gateway_rest_api" "api" {
  count = local.packages_ready ? 1 : 0
  name  = var.name_prefix
}

resource "aws_api_gateway_resource" "orders" {
  count       = local.packages_ready ? 1 : 0
  rest_api_id = aws_api_gateway_rest_api.api[0].id
  parent_id   = aws_api_gateway_rest_api.api[0].root_resource_id
  path_part   = "orders"
}

resource "aws_api_gateway_resource" "order_id" {
  count       = local.packages_ready ? 1 : 0
  rest_api_id = aws_api_gateway_rest_api.api[0].id
  parent_id   = aws_api_gateway_resource.orders[0].id
  path_part   = "{id}"
}

resource "aws_api_gateway_resource" "cancel" {
  count       = local.packages_ready ? 1 : 0
  rest_api_id = aws_api_gateway_rest_api.api[0].id
  parent_id   = aws_api_gateway_resource.order_id[0].id
  path_part   = "cancel"
}

resource "aws_api_gateway_method" "post_orders" {
  count         = local.packages_ready ? 1 : 0
  rest_api_id   = aws_api_gateway_rest_api.api[0].id
  resource_id   = aws_api_gateway_resource.orders[0].id
  http_method   = "POST"
  authorization = "NONE"
}

resource "aws_api_gateway_method" "get_order" {
  count         = local.packages_ready ? 1 : 0
  rest_api_id   = aws_api_gateway_rest_api.api[0].id
  resource_id   = aws_api_gateway_resource.order_id[0].id
  http_method   = "GET"
  authorization = "NONE"
}

resource "aws_api_gateway_method" "cancel_order" {
  count         = local.packages_ready ? 1 : 0
  rest_api_id   = aws_api_gateway_rest_api.api[0].id
  resource_id   = aws_api_gateway_resource.cancel[0].id
  http_method   = "POST"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "post_orders" {
  count                   = local.packages_ready ? 1 : 0
  rest_api_id             = aws_api_gateway_rest_api.api[0].id
  resource_id             = aws_api_gateway_resource.orders[0].id
  http_method             = aws_api_gateway_method.post_orders[0].http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.orders_api[0].invoke_arn
}

resource "aws_api_gateway_integration" "get_order" {
  count                   = local.packages_ready ? 1 : 0
  rest_api_id             = aws_api_gateway_rest_api.api[0].id
  resource_id             = aws_api_gateway_resource.order_id[0].id
  http_method             = aws_api_gateway_method.get_order[0].http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.orders_api[0].invoke_arn
}

resource "aws_api_gateway_integration" "cancel_order" {
  count                   = local.packages_ready ? 1 : 0
  rest_api_id             = aws_api_gateway_rest_api.api[0].id
  resource_id             = aws_api_gateway_resource.cancel[0].id
  http_method             = aws_api_gateway_method.cancel_order[0].http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.orders_api[0].invoke_arn
}

resource "aws_api_gateway_deployment" "prod" {
  count       = local.packages_ready ? 1 : 0
  rest_api_id = aws_api_gateway_rest_api.api[0].id

  triggers = {
    redeploy = sha1(jsonencode([
      aws_api_gateway_integration.post_orders[0].id,
      aws_api_gateway_integration.get_order[0].id,
      aws_api_gateway_integration.cancel_order[0].id,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [
    aws_api_gateway_integration.post_orders,
    aws_api_gateway_integration.get_order,
    aws_api_gateway_integration.cancel_order,
  ]
}

resource "aws_api_gateway_stage" "prod" {
  count                = local.packages_ready ? 1 : 0
  deployment_id        = aws_api_gateway_deployment.prod[0].id
  rest_api_id          = aws_api_gateway_rest_api.api[0].id
  stage_name           = "prod"
  xray_tracing_enabled = local.tracing_on
}

resource "aws_lambda_permission" "apigw" {
  count         = local.packages_ready ? 1 : 0
  statement_id  = "AllowApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.orders_api[0].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api[0].execution_arn}/*/*"
}

resource "aws_xray_sampling_rule" "faultlab" {
  count = local.tracing_on ? 1 : 0

  rule_name      = "${var.name_prefix}-sampling"
  priority       = 100
  version        = 1
  reservoir_size = var.sampling_reservoir
  fixed_rate     = var.sampling_fixed_rate
  url_path       = "*"
  host           = "*"
  http_method    = "*"
  service_type   = "*"
  service_name   = "${var.name_prefix}/prod"
  resource_arn   = "*"
}

resource "aws_budgets_budget" "monthly" {
  count = var.alert_email == "" ? 0 : 1

  name         = "${var.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
}
