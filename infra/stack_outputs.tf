output "table_name" { value = aws_dynamodb_table.orders.name }
output "fault_parameter_name" { value = aws_ssm_parameter.fault.name }
output "orders_api_function_name" {
  value = try(aws_lambda_function.orders_api[0].function_name, null)
}
output "api_url" {
  value = try("https://${aws_api_gateway_rest_api.api[0].id}.execute-api.${var.region}.amazonaws.com/prod", null)
}
