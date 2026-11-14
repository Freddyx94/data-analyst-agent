# Database schema: what the agent knows about the data

This file is read by the agent's tools. Edit it to change what the agent knows.

Format rules (the parser in `src/agent/schema_doc.py` depends on them):
- One `## schema.table` heading per table.
- Text under the heading is the table description.
- Each row of the column table is `| column | description |`.
- Do not use the `|` character inside a description.

Column types and row counts are not written here: the tools read them from the database.

## General notes

Walmart M5 data: one store (CA_1, California), category FOODS, 1,437 items.
Daily sales from 2011-01-29 to 2016-05-22. Forecasts for the 28 days after that.
Prices are in US dollars. Revenue is not stored: it is units_sold * sell_price.
The data has no stock levels. ml.stockout_days holds likely stockouts, which are estimates.
results.holdout_results holds the accuracy of the forecast models on a test period.

## marts.fct_daily_sales

Daily sales. One row per item, store and day, also for days when nothing was sold (units_sold = 0; almost half of all rows). An item's rows start at its launch_date (the start of the first week it had a price in the store), not at 2011-01-29. Zeros on or after launch_date are real: the item was on sale and nobody bought it. Dates run from 2011-01-29 to 2016-05-22. Join to marts.dim_date on date and to marts.dim_item on item_id.

| column | description |
|---|---|
| date | The day of the sale. |
| item_id | Item identifier, for example FOODS_3_090. |
| store_id | Store identifier. Always CA_1. |
| units_sold | Number of units sold that day. 0 when nothing was sold. Always 0 on days the store is closed. |
| sell_price | Price per unit in US dollars. Prices are set per week: the price is the same on all days of one Walmart week (wm_yr_wk). Never NULL. |

## marts.dim_date

Calendar. One row per day from 2011-01-29 to 2016-06-19. The last 28 days (from 2016-05-23) have no sales rows: they are the forecast period.

| column | description |
|---|---|
| date | The calendar day. |
| d | Day label from the original M5 files (d_1, d_2, ...). Not needed for analysis. |
| wm_yr_wk | Walmart week identifier, for example 11101. Walmart weeks run from Saturday to Friday. |
| weekday | Name of the weekday in English, for example Monday. |
| wday | Number of the weekday inside the Walmart week: 1 = Saturday, 2 = Sunday, ..., 7 = Friday. |
| month | Month number, 1 to 12. |
| year | Calendar year, 2011 to 2016. 2011 starts on 29 January and 2016 is not a full year. |
| is_event | True when the day has at least one holiday or special event. |
| event_name | Name of the event, for example Christmas or SuperBowl. NULL on normal days. A few days have two events: only the first listed one is kept here. |
| event_type | Type of the event in event_name: National, Religious, Cultural or Sporting. NULL on normal days. |
| is_snap | True on days when SNAP benefits (US government food assistance) can be spent in California. These are days 1 to 10 of every month. Food sales are often higher on these days. |
| is_store_closed | True when the store is closed. This is 25 December in 2011 to 2015 (5 days). Sales are 0 on these days. |

## marts.dim_item

Items. One row per item (1,437 items), all in category FOODS.

| column | description |
|---|---|
| item_id | Item identifier, for example FOODS_3_090. |
| dept_id | Department: FOODS_1, FOODS_2 or FOODS_3. |
| cat_id | Category. Always FOODS. |
| launch_week | Launch week: the first Walmart week (wm_yr_wk) in which the item had a price in this store. Having a price means the item was on the shelf and could be bought. |
| launch_date | First day (Saturday) of the launch_week. The item's rows in fct_daily_sales start on this day. Items that existed from the beginning have 2011-01-29. Launch is defined by the first price, not by the first sale. |
| first_sale_date | First day with at least one unit sold. Can be later than launch_date: between the two dates the item was on sale but sold nothing. |

## marts.dim_store

Stores. Only one row: store CA_1.

| column | description |
|---|---|
| store_id | Store identifier. Always CA_1. |
| state_id | US state. Always CA (California). |

## serving.forecasts

Sales forecast from a machine learning model. One row per item and day for the 28 days after the sales data ends: 2016-05-23 to 2016-06-19. These are predictions, not real sales.

| column | description |
|---|---|
| item_id | Item identifier. |
| date | The day the forecast is for. |
| forecast | Predicted units sold on that day. A decimal number, for example 0.64. |
| model_version | Name of the model that made the forecast. Only one value: lightgbm_20160522. |
| created_at | Technical timestamp: when the forecast row was written. Not a business date. |

## ml.stockout_days

Likely stockouts: days on which an item was probably out of stock. One row per item and flagged day (345,002 rows, 1,135 of the 1,437 items). IMPORTANT: these are not observed facts. The data has no stock levels. A day is flagged when it belongs to a run of 28 or more days in a row with zero sales that is statistically very unlikely for that item. Always say "likely stockout" in answers, never "stockout". Every flagged day has units_sold = 0 in marts.fct_daily_sales. Join to marts.fct_daily_sales on item_id and date.

| column | description |
|---|---|
| item_id | Item identifier. |
| date | The flagged day: a day inside a likely-stockout run. |
| run_start | First day of the zero-sales run this day belongs to. |
| run_end | Last day of the zero-sales run this day belongs to. |
| run_days | Length of the run in days, from run_start to run_end. Always 28 or more. The same value on every row of one run, so do not sum it over rows. |

## results.holdout_results

Accuracy of the forecast models on a hold-out test. One row per model (5 rows). Hold-out period: 2016-04-25 to 2016-05-22, the last 28 days of the sales data. Each model forecast these days without seeing them, and the forecast was compared with the real sales. The main score is rmsse. The main model is lightgbm: it made the forecasts in serving.forecasts. seasonal_naive, moving_average_28 and ets are simple comparison models. lightgbm_drop_stockouts is a variant trained without likely-stockout days, not the main model.

| column | description |
|---|---|
| model | Name of the model: lightgbm, seasonal_naive, moving_average_28, ets or lightgbm_drop_stockouts. |
| mae | Mean absolute error in units sold per item and day. Lower is better. |
| rmse | Root mean squared error in units sold per item and day. Lower is better. |
| rmsse | Root mean squared scaled error, the main score. Lower is better. It is the forecast error relative to each item's normal day-to-day variation, averaged over items. |
| weighted_rmsse | RMSSE as a weighted average over items instead of a simple average. Lower is better. |
| bias | Total forecast compared with total actual sales. -0.068 means the forecast was 6.8% too low in total, 0.02 means 2% too high. Closer to 0 is better. |
| rmsse_clean | rmsse calculated without likely-stockout days (see ml.stockout_days). |
| bias_clean | bias calculated without likely-stockout days. |
| rmsse_better_than_seasonal_naive_pct | By how many percent this model's rmsse is lower than the rmsse of seasonal_naive. Positive means better, negative means worse, 0 in the seasonal_naive row. |
| rmsse_better_than_moving_average_28_pct | By how many percent this model's rmsse is lower than the rmsse of moving_average_28. Positive means better, negative means worse. |
| rmsse_better_than_ets_pct | By how many percent this model's rmsse is lower than the rmsse of ets. Positive means better, negative means worse. |
