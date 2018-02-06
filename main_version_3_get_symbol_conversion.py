'''
import packages
'''
import sys
import json
from scipy import stats
import pandas as pd
import schedule
import psycopg2
from sqlalchemy import create_engine
import time
from pandas import DataFrame
import numpy as np
import requests
import datetime

'''
LP Raw Data
'''


def _get_symbol_conversion_():
    symbol_conversion = pd.read_excel(
        'symbolConversion.xlsx', sheet_name='symbolConversion')
    symbol_conversion_dict = symbol_conversion.set_index(
        'symbol_index').to_dict()['conversion_rate']
    return symbol_conversion_dict


def _get_access_token_():
    url = "https://38.76.4.235:44300/api/token"
    headers = {'content-type': 'application/x-www-form-urlencoded',
               'grant_type': 'password', 'username': 'APITest',
               'password': 'cw12345..'}
    request = requests.post(url, data=headers, verify=False)
    data = request.json()
    return data['access_token']


def _get_lp_position_(margin_account_number, access_token, symbol_conversion_dict):
    dollarized_value_dict = {}
    url_position = 'https://38.76.4.235:44300/api/rest/margin-account/' + \
        str(margin_account_number) + '/positions'
    url_equity = 'https://38.76.4.235:44300/api/rest/margin-account/' + \
        str(margin_account_number)
    request_position = requests.get(url_position, headers={
                                    'Authorization': 'Bearer ' + access_token}, verify=False)
    request_equity = requests.get(
        url_equity, headers={'Authorization': 'Bearer ' + access_token}, verify=False)
    data_position = json.loads(request_position.text)
    data_equity = json.loads(request_equity.text)

    free_margin = data_equity['freeMargin']
    margin = data_equity['margin']

    for symbol_position in data_position['data']:
        if margin_account_number == 22:
            symbol = symbol_position['coreSymbol'][1:]
        else:
            symbol = symbol_position['coreSymbol']
        position = float(symbol_position['position'])
        price = float(symbol_position['adapterPositions'][0]['marketPrice'])
        if symbol[3:6] == 'USD':
            dollarized_value_dict[symbol] = position * price
        elif(symbol[3:6] != 'USD' and symbol[0:3] != 'USD'):
            exchange_rate = float(symbol_conversion_dict[symbol[0:3]])
            dollarized_value_dict[symbol] = position * exchange_rate
        else:
            dollarized_value_dict[symbol] = position
    print(dollarized_value_dict)
    return free_margin, margin, dollarized_value_dict

'''
price return
'''
def _get_price_array_():
    var_template = pd.ExcelFile('VaR Template.xlsx')
    dframe_var_template = var_template.parse(
        '~Raw Date', index_col=0, header=0)
    columns_indexes = dframe_var_template.columns.values
    symbol_list = np.array(columns_indexes)
    return symbol_list, dframe_var_template

def _get_price_return_(price_array):
    #矩阵运算得到return
    price_return = price_array.pct_change() + 1
    price_return = np.log(price_return)
    price_return = DataFrame.as_matrix(price_return.dropna())
    return price_return

def _get_excess_return_(rows, columns, price_return):
    #获取均值方差
    return_statistic = np.zeros([4, columns])
    for column in range(columns):
        return_statistic[0][column] = np.mean(price_return[:, column])
        return_statistic[1][column] = rows
        return_statistic[2][column] = np.std(price_return[:, column])
        return_statistic[3][column] = np.var(price_return[:, column])
    #print('returnStatistic: \n', return_statistic)
    #获取减均值之后的矩阵
    for row in range(rows - 1):
        price_return[row] = price_return[row] - return_statistic[0]
    excess_return = price_return
    #print('excessReturn: \n', excess_return)
    return return_statistic, excess_return

def _get_var_cov_(rows, excess_return):
    #excessReturn 转置矩阵相乘
    var_cov = np.matmul(excess_return.T, excess_return, out=None) / (rows - 1)
    return var_cov

def _get_lp_var_cov_():
    #get price return
    symbol_list, price_array = _get_price_array_()
    rows = price_array.shape[0]
    columns = price_array.shape[1]
    print('rows: ', rows, 'columns: ', columns, '\n')
    price_return = _get_price_return_(price_array)
    return_statistic, excess_return = _get_excess_return_(
        rows, columns, price_return)
    var_cov = _get_var_cov_(rows, excess_return)
    return return_statistic, symbol_list, var_cov


def _get_lp_var_(symbol_list, var_cov, margin_account_number):
    #get LP positions
    access_token = _get_access_token_()
    symbol_conversion_dict = _get_symbol_conversion_()
    free_margin, margin, dollarized_value_dict = _get_lp_position_(
        margin_account_number, access_token, symbol_conversion_dict)
    #create weightage
    weightage = {}
    for symbol in symbol_list:
        weightage[symbol] = 0.0
    weightage.update(dollarized_value_dict)
    weightage_list = []
    for wvalue in weightage.values():
        weightage_list.append(wvalue)
    weightage_array = np.array(weightage_list)
    portfolio_value = np.sum(weightage_array)
    weightage_array = weightage_array / portfolio_value
    #矩阵相乘
    transit = np.matmul(weightage_array.T, var_cov, out=None)
    var = np.matmul(transit, weightage_array, out=None)
    return free_margin, margin, portfolio_value, weightage_array, var

def _get_lp_based_result_(portfolio_value, weightage_array, dev, return_statistic):
    '''
    get lp based pnl
    '''
    pnl_1 = []
    pnl_5 = []
    #one day
    std_dev = dev ** 0.5
    avg_return = float(np.matmul(return_statistic, weightage_array)[0])
    #one week
    std_dev_5 = (dev * 5) ** 0.5
    avg_return_5 = (avg_return + 1) ** 5 - 1
    print(avg_return, '\t', std_dev, '\t',
          avg_return_5, '\t', std_dev_5, '\t\n')
    confidence_lvls = [0.5, 0.4, 0.3, 0.2, 0.1, 0.05, 0.01]
    for confidence_lvl in confidence_lvls:
        #one day pnl list
        var_pl_1 = stats.norm.interval(
            confidence_lvl, avg_return, std_dev)[0] * portfolio_value
        pnl_1.append(var_pl_1)
        #five day pnl list
        var_pl_5 = stats.norm.interval(
            confidence_lvl, avg_return_5, std_dev_5)[0] * portfolio_value
        pnl_5.append(var_pl_5)
    return avg_return, std_dev, std_dev_5, avg_return_5, pnl_1, pnl_5

def _get_monte_carlo_result_(portfolio_value, avg_return, std_dev):
    '''
    get monte carlo result based on lp positions
    '''
    initial_index = 1.0    # 股票或指数初始的价格;
    i = 10000       # number of simulation
    time_step = 1.0  # 期权的到期年限(距离到期日时间间隔)
    number_of_time_step = 50         # number of time steps
    time_interval = time_step / number_of_time_step       # time enterval

    # 20000条模拟路径，每条路径５０个时间步数
    return_array = np.zeros((number_of_time_step + 1, i))
    return_array[0] = initial_index
    for time_step in range(1, number_of_time_step + 1):
        random_array = np.random.standard_normal(i)
        return_array[time_step] = return_array[time_step - 1] * np.exp(
            (avg_return - 0.5 * std_dev ** 2) *
            time_interval + std_dev * np.sqrt(time_interval) * random_array)
    rank_return = return_array[-1] - 1
    rank_return_df = pd.DataFrame(rank_return * portfolio_value)
    mc_pnl = []
    mc_c_pnl = []
    for confidence_lvl in [50, 40, 30, 20, 10, 5, 1]:
        percentile_pnl = np.percentile(rank_return_df, confidence_lvl)
        mc_pnl.append(percentile_pnl)
        mc_c_pnl.append(
            rank_return_df[rank_return_df < percentile_pnl].mean()[0])
    return mc_pnl, mc_c_pnl

'''
#post data
def _get_bi_access_token_():
    #get power bi access token
    get_access_token_url = 'https://login.microsoftonline.com/common/oauth2/token'
    access_token_headers = {'grant_type': 'password', 'scope': 'openid',
                            'resource': 'https://analysis.windows.net/powerbi/api',
                            'client_id': '68817d3c-cd2d-4fc3-a602-b77374095798',
                            'username': 'prime@blackwellglobal.com',
                            'password': 'June123me'}
    access_token_request = requests.post(
        get_access_token_url, data=access_token_headers, verify=False)
    access_token_response = access_token_request.json()
    bi_access_token = access_token_response['access_token']
    return bi_access_token

def _get_bi_dataset_id_(bi_access_token):
    #get dataset id
    get_dataset_name_url = 'https://api.powerbi.com/v1.0/myorg/datasets'
    dataset_name_request = requests.get(get_dataset_name_url, headers={
                                        'Authorization': 'Bearer ' + bi_access_token})
    dataset_name_response = dataset_name_request.json()
    for value in dataset_name_response['value']:
        if value['name'] == 'VaR':
            dataset_id = value['id']
    return dataset_id

def _post_data_to_bi_(dataset_id, bi_access_token, data):
    #push data
    pushdata_url_list = [
        'https://api.powerbi.com/v1.0/myorg/datasets/', dataset_id, '/tables/LP VaR/rows']
    pushdata_url = ''.join(pushdata_url_list)
    requests.post(pushdata_url, headers={
        'Authorization': 'Bearer ' + bi_access_token}, json={"rows": [data]})
'''

def _save_lp_var_(lp_var_info):
    # create_engine说明：dialect[+driver]://user:password@host/dbname[?key=value..]
    df_lp_var_info = DataFrame(lp_var_info)
    engine = create_engine(
        'postgresql://postgres:12345@localhost:5432/VaR')
    print(df_lp_var_info)
    df_lp_var_info.to_sql("LP VaR", engine,
                          index=False, if_exists='append')

def main(argv=None):
    '''
    calculate var
    '''
    if argv is None:
        argv = sys.argv

    return_statistic, symbol_list, var_cov = _get_lp_var_cov_()

    margin_account_numbers = [11]
    mc_lp = {10:'LMAX', 11:'Divisa'}
    #margin_account_numbers = [9, 10, 11, 13, 22]

    for margin_account_number in mc_lp.keys():
        free_margin, margin, portfolio_value, weightage_array, dev = _get_lp_var_(
            symbol_list, var_cov, margin_account_number)
        avg_return, std_dev, std_dev_5, avg_return_5, pnl_1, pnl_5 = _get_lp_based_result_(
            portfolio_value, weightage_array, dev, return_statistic)
        mc_pnl, mc_c_pnl = _get_monte_carlo_result_(
            portfolio_value, avg_return, std_dev)
        mc_pnl_5, mc_c_pnl_5 = _get_monte_carlo_result_(
            portfolio_value, avg_return_5, std_dev_5)
        equity = float(free_margin) + float(margin)
        lp_var_info_day = {'timestamp':[datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), datetime.datetime.now().strftime("%Y-%m-%d %H:%M")], 
            'LP': [mc_lp[margin_account_number], mc_lp[margin_account_number]], 
            'free equity': [float(free_margin), float(free_margin)],
            'margin': [float(margin), float(margin)],
            'c8': [equity + mc_c_pnl[-4], equity + mc_c_pnl_5[-4]],
            'c9': [equity + mc_c_pnl[-3], equity + mc_c_pnl_5[-3]],
            'c95': [equity + mc_c_pnl[-2], equity + mc_c_pnl_5[-2]],
            'type': ['one day', 'one week']}
        print(lp_var_info_day)
        _save_lp_var_(lp_var_info_day)
        '''
        bi_access_token = _get_bi_access_token_()
        dataset_id = _get_bi_dataset_id_(bi_access_token)
        _post_data_to_bi_(dataset_id, bi_access_token, lp_var_info_day)
        _post_data_to_bi_(dataset_id, bi_access_token, lp_var_info_week)
        print('yeah!!!')
        '''


if __name__ == "__main__":
    schedule.every().hour.do(main)

while True:
    schedule.run_pending()
    time.sleep(1)