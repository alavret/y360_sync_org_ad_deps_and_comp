import os
from dotenv import load_dotenv
from datetime import datetime
from ldap3 import Server, Connection, ALL, SUBTREE, ALL_ATTRIBUTES, Tls, MODIFY_REPLACE, set_config_parameter
from ldap3.core.exceptions import LDAPBindError
from lib.y360_api.api_script import API360
import logging
import logging.handlers as handlers

LOG_FILE = "sync_deps.log"

logger = logging.getLogger("sync_deps")
logger.setLevel(logging.DEBUG)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter('%(asctime)s.%(msecs)03d %(levelname)s:\t%(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
#file_handler = handlers.TimedRotatingFileHandler(LOG_FILE, when='D', interval=1, backupCount=30, encoding='utf-8')
file_handler = handlers.RotatingFileHandler(LOG_FILE, maxBytes=10 * 1024 * 1024,  backupCount=20, encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter('%(asctime)s.%(msecs)03d %(levelname)s:\t%(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
logger.addHandler(console_handler)
logger.addHandler(file_handler)

def get_ldap_users():

    set_config_parameter('DEFAULT_SERVER_ENCODING', 'utf-8')
    set_config_parameter('ADDITIONAL_SERVER_ENCODINGS', 'koi8-r')


    ldap_host = os.environ.get('LDAP_HOST')
    ldap_port = int(os.environ.get('LDAP_PORT'))
    ldap_user = os.environ.get('LDAP_USER')
    ldap_password = os.environ.get('LDAP_PASSWORD')
    ldap_base_dn = os.environ.get('LDAP_BASE_DN')
    ldap_search_filter = os.environ.get('LDAP_SEARCH_FILTER')

    attrib_list = list(os.environ.get('ATTRIB_LIST').split(','))
    out_file = os.environ.get('AD_DEPS_OUT_FILE')

    users = {}
    server = Server(ldap_host, port=ldap_port, get_info=ALL) 
    try:
        conn = Connection(server, user=ldap_user, password=ldap_password, auto_bind=True)
    except LDAPBindError as e:
        logger.error('Can not connect to LDAP - "automatic bind not successful - invalidCredentials". Exit.')
        logger.error(f"{type(e).__name__} at line {e.__traceback__.tb_lineno} of {__file__}: {e}")
        return {}
            
    users = {}
    conn.search(ldap_base_dn, ldap_search_filter, search_scope=SUBTREE, attributes=attrib_list)
    if conn.last_error is not None:
        logger.error(f'Can not connect to LDAP. Exit.')
        logger.error(f"{type(e).__name__} at line {e.__traceback__.tb_lineno} of {__file__}: {e}")
        return {}

    try:            
        for item in conn.entries:
            if item['mail'].value is not None:
                if len(item['mail'].value.strip()) > 0:
                    department = ''
                    if item['department'].value is not None:
                        if len(item['department'].value.strip()) > 0:
                            department = item['department'].value.strip()
                    company = ''
                    if item['company'].value is not None:
                        if len(item['company'].value.strip()) > 0:
                            company = item['company'].value.strip()

                    if len(department) > 0 and len(company) > 0:
                        users[item['mail'].value.lower().strip()] = f'{department.strip()} ({company.strip()})'
                    else:
                        users[item['mail'].value.lower().strip()] = ''
                        logger.debug(f'User {item["mail"].value} has empty department or company. Skip.')

    except Exception as e:
        logger.error(f"{type(e).__name__} at line {e.__traceback__.tb_lineno} of {__file__}: {e}")
        return {}
        
    if out_file:
        with open(out_file, "w", encoding="utf-8") as f:
            f.write("mail;department (company)\n")
            for k,v in users.items():
                f.write(f"{k};{v}\n")

    return users

def get_file_users():

    file = os.environ.get('USERS_FILE')
    users = {}
    try:
        with open(file, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line and ';' in line:
                    mail, department = line.split(";")
                    users[mail.strip()] = department.strip()
    except Exception as e:
        logger.error(f"{type(e).__name__} at line {e.__traceback__.tb_lineno} of {__file__}: {e}")
    
    return users

def generate_deps_list_from_api():
    all_deps_from_api = organization.get_departments_list()
    if len(all_deps_from_api) == 1:
        #print('There are no departments in organozation! Exit.')
        return {}
    all_deps = {'1' : 'All'}
    for item in all_deps_from_api:  
        all_deps[item['id']] = item['name'].strip()

    return all_deps

def add_new_deps_to_y360(new_deps):
    for item in new_deps:
        department_info = {
                    "name": item,
                    "parentId": 1
                }
        logger.info(f'Adding department {item} to Y360')
        if not dry_run:
            organization.post_create_department(department_info)
    new_deps = generate_deps_list_from_api()
    return new_deps

def compare_with_y360():    
    users_org = {}
    users_id = {}
    users_aliases = {}

    #onprem_users = get_file_users()
    onprem_users = get_ldap_users()
    if not onprem_users:
        logger.info('List of local users is empty. Exit.')
        return
    else:
        logger.info(f'Got list of local users. Total count: {len(onprem_users)}')
    
    temp_deps = {k: v for k, v in onprem_users.items() if v}
    onprem_deps_set =  set(temp_deps.values())
    if not onprem_deps_set:
        logger.info('List of local departments is empty. Exit.')
        return
    else:
        logger.info(f'Got list of local departments. Total count: {len(onprem_deps_set)}')
    
    online_deps = generate_deps_list_from_api()
    if not online_deps:
        logger.info('List of Y360 departments is empty. Exit.')
    else:
        logger.info(f'Got list of Y360 departments. Total count: {len(online_deps)}')

    
    online_deps_set = set(online_deps.values())
    diff_set = onprem_deps_set.difference(online_deps_set)
    if diff_set:
        #saveToLog(message=f'List of local departments is not equal to Y360 departments. Add new departments.', status='Warning', console=console)
        for dep in diff_set:
            logger.info(f'Dep for adding to Y360 - {dep}')
        #return
    else:
        logger.info(f'All deps in local catalog exist in Y360')

    diff_set2 = online_deps_set.difference(onprem_deps_set)
    if diff_set2:
        #saveToLog(message=f'List of local departments is not equal to Y360 departments. Add new departments.', status='Warning', console=console)
        for dep in diff_set2:
            logger.info(f'Dep in local catalog that not exist in Y360 - {dep}')
        #return
    else:
        logger.info(f'All deps in Y360 exist in local catalog')

    deps = add_new_deps_to_y360(diff_set)

    for user in organization.get_all_users():
        users_org[user['email']] = user['departmentId']
        users_id[user['email']] = user['id']
        users_aliases[user['email']] = user['aliases']

    if not users_org:
        logger.info('List of Y360 users is empty. Exit.')
        return
    else:
        logger.info(f'Got list of Y360 users. Total count: {len(users_org)}')

    missed_emails = []
    try:
        for email in users_org.keys():
            if email in onprem_users: 
                #print(f"{email} - {onprem_users[email]}")   
                if len(onprem_users[email].strip()) > 0 :
                    if onprem_users[email].strip() != deps[users_org[email]]:
                        #new_deps = (k,v for k,v in deps.items() if v == onprem_users[email].strip())
                        new_deps_id = list(deps.keys())[list(deps.values()).index(onprem_users[email].strip())]
                        logger.info(f'Try to change department of {email} user from _ {deps[users_org[email]]} _ to _ {onprem_users[email]} _')
                        if not dry_run:
                            organization.patch_user_info(
                                uid = users_id[email],
                                user_data={
                                    "departmentId": new_deps_id,
                                })
                else:
                    if users_org[email] != 1:
                        logger.info(f'Try to change department of {email} user from _ {deps[users_org[email]]} _ to _ All _')
                        if not dry_run:
                            organization.patch_user_info(
                                    uid = users_id[email],
                                    user_data={
                                        "departmentId": 1,
                                    })
            else:
                missed_emails.append(email)
                
        if missed_emails:
            for email in missed_emails:
                #saveToLog(message=f'Users not found in local catalog: {email}. Start searching in aliases.', status='Info', console=console)
                domain = email.split('@')[1]
                for alias in users_aliases[email]:
                    search_mail = alias + '@' + domain
                    if search_mail in onprem_users.keys():
                        logger.info(f'Found user _ alias _ in local catalog: {search_mail}. Start changing department.')
                        if not (len(onprem_users[search_mail].strip()) == 0 or onprem_users[search_mail].strip() =='[]') :
                            if onprem_users[search_mail].strip() != deps[users_org[email]]:
                                new_deps_id = list(deps.keys())[list(deps.values()).index(onprem_users[search_mail].strip())]
                                logger.info(f'Try to change department of {search_mail} user from _ {deps[users_org[email]]} _ to _ {onprem_users[search_mail]} _')
                                if not dry_run:
                                    organization.patch_user_info(
                                        uid = users_id[email],
                                        user_data={
                                            "departmentId": new_deps_id,
                                        })
                        else:
                            if users_org[email] != 1:
                                logger.info(f'Try to change department of {search_mail} user from _ {deps[users_org[email]]} _ to _ All _')
                                if not dry_run:
                                    organization.patch_user_info(
                                            uid = users_id[email],
                                            user_data={
                                                "departmentId": 1,
                                            })

    except Exception as e:
        logger.error(f"{type(e).__name__} at line {e.__traceback__.tb_lineno} of {__file__}: {e}")
    
    return

if __name__ == "__main__":
    denv_path = os.path.join(os.path.dirname(__file__), '.env_ldap')

    if os.path.exists(denv_path):
        load_dotenv(dotenv_path=denv_path,verbose=True, override=True)
    
    organization = API360(os.environ.get('orgId'), os.environ.get('access_token'))

    dry_run = False
    if os.environ.get('DRY_RUN'):
        if os.environ.get('DRY_RUN').lower() == 'true' or os.environ.get('DRY_RUN').lower() == '1':
            dry_run = True

    logger.info('---------------Start-----------------')
    if dry_run:
        logger.info('- Режим тестового прогона включен (DRY_RUN = True)! Изменеия не сохраняются! -')

    compare_with_y360()

    logger.info('---------------End-----------------')
    #users = get_file_users(users_file)
    #print(users)
    #compare_with_y360()
  